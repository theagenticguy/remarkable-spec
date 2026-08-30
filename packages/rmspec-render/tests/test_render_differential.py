"""Thirty pinned SVG hashes: a regression guard, no longer a differential.

``tests/fixtures/render-differential-manifest.json`` records, for each of thirty ``.rm`` files
from one device's backup, its content hash, its layer and stroke counts, the screen the legacy
``detect_screen`` chose, and the SHA-256 of the SVG the legacy renderer wrote at thickness 1.5
with page uuid ``UUID(int=0)``. This module walks the corpus, renders every entry through
``rmspec.formats`` and ``rmspec.render``, and compares hashes. Byte-exactness is the
requirement: attribute order, float formatting, whitespace and path precision all count, even
when the picture is identical.

What changed when the legacy tree was deleted, and what it costs
---------------------------------------------------------------
Until 2026-08-30 this module was a true differential: it parsed each file with
``src/remarkable_spec/formats/rm_file.py`` and rendered it through the relocated renderer, so
two independent implementations stood behind every hash. That tree is gone. The thirty hashes
are unchanged and still asserted byte for byte, but nothing re-derives them any more -- they are
a **recorded oracle**, and this module is a regression pin on ``rmspec.render``'s own output.
A reader must not take a green run here as evidence that a second implementation agrees.

The agreement was real and was verified at the last possible moment. On 2026-08-30, with the
legacy tree still present, all thirty entries reproduced byte-identically through the legacy
parse; the parse was then re-pointed at :class:`rmspec.formats.SceneCodec` and all thirty
reproduced again, unchanged. That second run is the stronger statement of the two: identical
SVG bytes from a different codec's geometry means the two codecs agreed on every point, not
merely on the layer and stroke counts the manifest records. Those counts are still asserted
first, in ``test_the_decode_matches_the_manifest_counts``, so a codec regression is attributed
to the codec rather than surfacing as a render hash mismatch.

Two capabilities went with the tree. The mismatch diagnostic can no longer report the first
differing byte offset, because the recorded SVG bytes were never committed -- only their hash
and length -- so there is nothing to diff against; ``_diagnose`` says so in its own output
rather than leaving a reader to wonder. And the corpus-scale record of the legacy loop's
behaviour on an empty stub (sixty-two bare ``EOFError``s) has no code left to assert it; it
survives in the manifest's ``empty_stub_class`` block and in the docstring of
``test_no_zero_byte_stub_is_a_renderable_entry``, which now pins the replacement behaviour.

Skipped, not weakened, when the corpus is absent
------------------------------------------------
The source files are a personal backup outside the repository (``~/remarkable`` by default,
overridable with ``RMSPEC_DIFFERENTIAL_CORPUS``), and neither they nor the rendered SVGs are
committed. Marked ``slow`` so the pre-commit fast lane skips it, and skipped when the corpus is
missing -- and three of the thirty hashes are additionally reproduced with no corpus at all,
from hand-built pages, in ``test_render_svg_document.py``. That is what keeps a clean CI machine
from silently testing nothing.

Why this reaches across to ``rmspec.formats``
---------------------------------------------
Getting from ``.rm`` bytes to a domain page needs a codec, and ``rmspec-render``'s declared
edge set is ``{domain}``. The edge that matters is the one
``tests/architecture/test_dependency_direction.py`` enforces, and it scans ``src`` only: no
module under ``packages/rmspec-render/src`` imports ``rmspec.formats``, and none may. A test
composing two slices is how ``rmspec-cli``'s suite already works, and composing them is the
only way this oracle can exist at all now that the legacy reader is gone.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from render_builders import LEGACY_STYLE, ZERO_PAGE_UUID

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Page,
    PageContent,
    PageId,
    PenColor,
    ScreenSpec,
)
from rmspec.formats import SceneCodec, fingerprint_bytes
from rmspec.render import SvgPageRenderer

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "tests" / "fixtures" / "render-differential-manifest.json"
CORPUS = Path(os.environ.get("RMSPEC_DIFFERENTIAL_CORPUS", "~/remarkable")).expanduser()

SCREENS: dict[str, ScreenSpec] = {
    "1620x2160": PAPER_PRO_SCREEN,
    "1404x1872": RM2_SCREEN,
}

EXPECTED_FILES = 92
EXPECTED_EMPTY_STUBS = 62
EXPECTED_RENDERABLE = 30

#: How much of the rendered document ``_diagnose`` quotes at each end. The envelope deltas a
#: moved hash usually means -- the XML declaration, the closing tag, a trailing newline -- all
#: live within this many bytes of one boundary or the other.
DIAGNOSTIC_WINDOW = 160

requires_corpus = pytest.mark.skipif(
    not CORPUS.is_dir(),
    reason=(
        f"needs the reference corpus at {CORPUS}; three of the thirty hashes are covered "
        f"corpus-free in test_render_svg_document.py"
    ),
)


class Entry(NamedTuple):
    """One manifest row, plus the file on disk whose bytes hash to it."""

    path: Path
    rm_sha256: str
    layers: int
    strokes: int
    screen: str
    svg_sha256: str
    svg_bytes: int


def _manifest() -> dict[str, dict[str, Any]]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {row["rm_sha256"]: row for row in payload["entries"]}


def _corpus_files() -> list[Path]:
    return sorted(CORPUS.rglob("*.rm"))


def _decode(path: Path) -> PageContent:
    """Decode one scene file with the codec that replaced the manifest's reader.

    Parameters
    ----------
    path
        Location of a ``.rm`` file in the corpus.

    Returns
    -------
    PageContent
        Layers in render order, the page's typed text, and any accepted defects.
    """
    return SceneCodec().decode_page(path.read_bytes(), path.name)


def _to_page(content: PageContent) -> Page:
    """Wrap decoded content at the page identity the manifest's ``render_params`` names.

    No field mapping any more: the codec already returns domain values, which is the whole
    point of the relocation. What used to be a ten-line transcription of the legacy reader's
    models is now this.

    Parameters
    ----------
    content
        Decoded page content.

    Returns
    -------
    Page
        The page, at index 0 with page uuid ``UUID(int=0)``.
    """
    return Page(page_id=PageId(uuid=ZERO_PAGE_UUID), index=0, content=content)


def _entries() -> Iterator[Entry]:
    rows = _manifest()
    for path in _corpus_files():
        digest = fingerprint_bytes(path.read_bytes())
        row = rows.get(digest)
        if row is None:
            continue
        yield Entry(
            path=path,
            rm_sha256=digest,
            layers=row["layers"],
            strokes=row["strokes"],
            screen=row["screen"],
            svg_sha256=row["svg_sha256"],
            svg_bytes=row["svg_bytes"],
        )


ENTRIES: list[Entry] = list(_entries()) if CORPUS.is_dir() else []


@requires_corpus
def test_the_corpus_has_the_shape_the_manifest_describes() -> None:
    """A shrunken corpus is a named failure, not a quietly smaller test."""
    files = _corpus_files()
    empty = [path for path in files if path.stat().st_size == 0]
    non_empty = [path for path in files if path.stat().st_size > 0]
    rows = _manifest()

    assert len(files) == EXPECTED_FILES
    assert len(empty) == EXPECTED_EMPTY_STUBS
    assert len(non_empty) == EXPECTED_RENDERABLE
    assert len(rows) == EXPECTED_RENDERABLE

    unknown = [path.name for path in non_empty if fingerprint_bytes(path.read_bytes()) not in rows]
    assert not unknown, f"corpus files with no manifest entry: {unknown}"
    assert len(ENTRIES) == EXPECTED_RENDERABLE


@requires_corpus
def test_no_zero_byte_stub_is_a_renderable_entry() -> None:
    """Why the oracle has thirty entries and not ninety-two, checked rather than assumed.

    Two thirds of a real document is zero-byte stubs -- the unannotated pages of a PDF-backed
    notebook. This test states the arithmetic the rest of the module depends on: every one of
    the sixty-two decodes to a page with no ink and no defect, and none of them appears in the
    manifest, so none contributes a hash.

    This is also where a deleted record used to live. Until 2026-08-30 the same sixty-two files
    were fed to the legacy reader to pin its *defect*: each raised a bare, message-less
    ``EOFError``, so a blank page and a truncated file were indistinguishable to the caller.
    That reader is gone and nothing can assert what it did any more. The finding survives in
    the manifest's ``empty_stub_class`` block, and what is checked here is the behaviour that
    replaced it -- a stub is a readable page with no ink, which is what makes the layer-less
    document pinned in ``test_render_svg_document.py`` the right thing to render for one.
    """
    stubs = [path for path in _corpus_files() if path.stat().st_size == 0]
    assert len(stubs) == EXPECTED_EMPTY_STUBS

    rows = _manifest()
    not_blank = [
        f"{path.name}: decoded {content!r}"
        for path in stubs
        if (content := _decode(path)) != PageContent()
    ]
    assert not not_blank, "\n".join(not_blank)

    hashed = [path.name for path in stubs if fingerprint_bytes(path.read_bytes()) in rows]
    assert not hashed, f"a zero-byte stub carries a recorded SVG hash: {hashed}"


@requires_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=[entry.rm_sha256[:12] for entry in ENTRIES])
def test_the_decode_matches_the_manifest_counts(entry: Entry) -> None:
    """Asserted before any hash, so a codec regression is attributed to the codec.

    These two numbers are the only per-page facts the manifest carries besides the SVG hash,
    and they are what the legacy reader recorded. A relocated codec that read a different
    number of strokes would move every hash on the page; failing here first says which half
    moved.
    """
    content = _decode(entry.path)
    assert len(content.layers) == entry.layers
    assert sum(len(layer.strokes) for layer in content.layers) == entry.strokes


@requires_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=[entry.rm_sha256[:12] for entry in ENTRIES])
def test_the_rendered_svg_matches_the_recorded_hash(entry: Entry) -> None:
    """The hardest gate. The comparison is a hash of bytes and is never relaxed."""
    page = _to_page(_decode(entry.path))
    rendered = SvgPageRenderer().render(
        page,
        screen=SCREENS[entry.screen],
        palette=EXPORT_PALETTE,
        style=LEGACY_STYLE,
    )
    raw = rendered.svg.encode()
    digest = hashlib.sha256(raw).hexdigest()

    if digest != entry.svg_sha256:
        pytest.fail(_diagnose(entry, raw, page))
    assert len(raw) == entry.svg_bytes


def _diagnose(entry: Entry, raw: bytes, page: Page) -> str:
    """Explain a mismatch as precisely as the recording allows, and say where that stops.

    Reports the byte-length delta first -- a constant 39 names the XML declaration, 1 names a
    trailing newline, anything larger names geometry -- then the hash pair, then three
    classifiers for the known suspects, then both ends of the document that was actually
    produced.

    No byte offset, and that is a consequence of the deletion rather than an omission. While
    the legacy tree existed this function re-rendered the entry with the legacy engine and
    reported the first differing offset against real reference bytes. The manifest records only
    a hash and a length -- the SVGs were never committed, deliberately, because they are a
    personal backup -- so once that engine was gone there was nothing left to diff against.
    The report says so, because a reader who has just watched a hash move will otherwise go
    looking for the diff.

    Parameters
    ----------
    entry
        The manifest row whose hash did not reproduce.
    raw
        The bytes this renderer produced.
    page
        The page those bytes were rendered from, for the suspect classifiers.

    Returns
    -------
    str
        The multi-line report.
    """
    delta = len(raw) - entry.svg_bytes
    content = page.content
    highlight = any(
        stroke.color is PenColor.HIGHLIGHT
        for layer in (content.layers if content else ())
        for stroke in layer.strokes
    )
    text_blocks = sum(
        1
        for layer in (content.layers if content else ())
        for block in layer.text_blocks
        if block.text.strip()
    ) + sum(1 for block in (content.text_blocks if content else ()) if block.text.strip())
    return "\n".join(
        [
            (
                f"{entry.rm_sha256[:12]} rendered {len(raw)} bytes, "
                f"manifest says {entry.svg_bytes} (delta {delta:+d})"
            ),
            f"sha256 {hashlib.sha256(raw).hexdigest()}, manifest says {entry.svg_sha256}",
            f"screen={entry.screen} layers={entry.layers} strokes={entry.strokes}",
            f"contains a PenColor.HIGHLIGHT stroke: {highlight}",
            f"drawable non-empty text blocks, both sources: {text_blocks}",
            (
                "no byte offset: the recorded SVG bytes were never committed, only their hash "
                "and length, so there is nothing to diff against. Both ends of what was "
                "produced follow"
            ),
            f"  head: {raw[:DIAGNOSTIC_WINDOW]!r}",
            f"  tail: {raw[-DIAGNOSTIC_WINDOW:]!r}",
        ]
    )


@requires_corpus
def test_no_oracle_entry_carries_a_highlight_stroke_or_typed_text() -> None:
    """The two divergences that *could* have broken byte-exactness, measured rather than assumed.

    The legacy ``RM_PALETTE`` had no entry for ``PenColor.HIGHLIGHT`` (9) and its ``get_rgb``
    fell back to black, while the domain's ``EXPORT_PALETTE`` is validated total and maps it to
    ``(251, 247, 25)``; and the legacy renderer drew no typed text at all. Either would move
    hashes if the corpus contained one. It contains neither -- every stroke in all thirty pages
    is ``PenColor.BLACK`` -- so ``EXPORT_PALETTE`` is used unmodified and text is drawn, with no
    manifest regeneration and no re-introduced fallback.

    Both text sources are counted, not just the layer-owned one. ``PageContent.text_blocks`` is
    now drawn too, and it is the source real typed text arrives on, so counting only
    ``Layer.text_blocks`` here would leave the claim "no oracle entry carries typed text"
    asserted over the source that never had any and unasserted over the one that could.
    """
    colours: set[int] = set()
    layer_blocks = 0
    page_blocks = 0
    for entry in ENTRIES:
        content = _decode(entry.path)
        page_blocks += sum(1 for block in content.text_blocks if block.text.strip())
        for layer in content.layers:
            colours.update(int(stroke.color) for stroke in layer.strokes)
            layer_blocks += sum(1 for block in layer.text_blocks if block.text.strip())

    assert PenColor.HIGHLIGHT.value not in colours, (
        "a colour-9 stroke would render black in the oracle and yellow here; inject a "
        "legacy-replica palette into this harness rather than regenerating the manifest"
    )
    assert (layer_blocks, page_blocks) == (0, 0), (
        "a drawable text block would make byte-exactness and the port's anti-vanishing rule "
        "mutually exclusive for that entry"
    )


@requires_corpus
def test_no_corpus_file_at_all_carries_typed_text() -> None:
    """The stronger form of the claim above, over all ninety-two files rather than the thirty.

    The thirty hashed entries are what byte-exactness is asserted against, but the reason the
    ``svg-v2`` -> ``svg-v3`` revision bump moved no hash is a fact about the whole corpus: not
    one of the ninety-two files carries a page-level block or a layer-owned one. Stated over
    every file, including the sixty-two zero-byte stubs, because "no hash moved" is a claim
    about what was measured and a reader has to be able to see the measurement.
    """
    page_blocks = 0
    layer_blocks = 0
    for path in _corpus_files():
        content = _decode(path)
        page_blocks += len(content.text_blocks)
        layer_blocks += sum(len(layer.text_blocks) for layer in content.layers)

    assert (page_blocks, layer_blocks) == (0, 0), (
        "a corpus file now carries typed text; re-check the thirty recorded SVG hashes before "
        "reading a green differential run as proof that drawing it changed nothing"
    )


@requires_corpus
def test_the_mismatch_diagnostic_names_the_delta_and_the_missing_reference() -> None:
    """A precise failure is the deliverable, so the failure path is exercised on purpose.

    ``_diagnose`` only runs when a hash moves, which is exactly when nobody wants to discover
    it raises. Fed a deliberately corrupted rendering, it must report the byte delta, the hash
    pair, the two classifiers, and the fact that no offset can be given -- rather than blowing
    up. The last of those is asserted because it is the one thing a reader will look for and
    not find: it was there while the legacy engine could regenerate reference bytes.
    """
    entry = ENTRIES[0]
    page = _to_page(_decode(entry.path))
    corrupted = b"<svg>not what the oracle says</svg>"
    report = _diagnose(entry, corrupted, page)

    assert entry.rm_sha256[:12] in report
    assert f"manifest says {entry.svg_bytes}" in report
    assert entry.svg_sha256 in report
    assert "no byte offset" in report
    assert "PenColor.HIGHLIGHT" in report
    assert "text blocks" in report
    assert repr(corrupted) in report
