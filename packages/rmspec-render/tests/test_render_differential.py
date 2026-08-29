"""The differential oracle: thirty real pages, reproduced byte for byte.

``tests/fixtures/render-differential-manifest.json`` records, for each of thirty ``.rm`` files
from one device's backup, its content hash, its layer and stroke counts, the screen the legacy
``detect_screen`` chose, and the SHA-256 of the SVG the legacy renderer wrote at thickness 1.5
with page uuid ``UUID(int=0)``. This module walks the corpus, renders every entry through the
relocated code, and compares hashes. Byte-exactness is the requirement: attribute order, float
formatting, whitespace and path precision all count, even when the picture is identical.

Skipped, not weakened, when the inputs are absent
-------------------------------------------------
The source files are a personal backup outside the repository (``~/remarkable`` by default,
overridable with ``RMSPEC_DIFFERENTIAL_CORPUS``), and neither they nor the rendered SVGs are
committed. Marked ``slow`` so the pre-commit fast lane skips it, and skipped when the corpus or
the legacy tree is missing -- and three of the thirty hashes are additionally reproduced with no
corpus at all, from hand-built pages, in ``test_render_svg_document.py``. That is what keeps a
clean CI machine from silently testing nothing.

Why this parses with the legacy reader
--------------------------------------
Reproducing legacy *bytes* requires the legacy *parse*: the manifest's layer and stroke counts
were produced by ``src/remarkable_spec/formats/rm_file.py``, and using a second codec would
conflate a parse difference with a render difference. This module therefore loads that reader
directly and converts its result into domain values, which is a ten-line mapping asserted
against the manifest's own counts before any hash is compared. When the legacy tree is deleted
at the end of the restructure, re-point ``_parse_layers`` at ``rmspec.formats`` -- the counts
assertion is what will tell you whether the two codecs agree.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import UUID

import pytest
from render_builders import LEGACY_STYLE, ZERO_PAGE_UUID

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Layer,
    Page,
    PageContent,
    PageId,
    PenColor,
    PenType,
    Point,
    ScreenSpec,
    Stroke,
    TextBlock,
)
from rmspec.render import SvgPageRenderer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    # The legacy reader's own models, for annotations only. They are never imported at run
    # time -- the module is loaded through importlib after the repository's ``src`` directory
    # is put on the path -- and this block is what to re-point at ``rmspec.formats`` when the
    # legacy tree is deleted.
    from remarkable_spec.models.page import Layer as LegacyLayer
    from remarkable_spec.models.page import TextBlock as LegacyTextBlock

pytestmark = pytest.mark.slow

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "tests" / "fixtures" / "render-differential-manifest.json"
LEGACY_SRC = REPO / "src"
CORPUS = Path(os.environ.get("RMSPEC_DIFFERENTIAL_CORPUS", "~/remarkable")).expanduser()

SCREENS: dict[str, ScreenSpec] = {
    "1620x2160": PAPER_PRO_SCREEN,
    "1404x1872": RM2_SCREEN,
}

EXPECTED_FILES = 92
EXPECTED_EMPTY_STUBS = 62
EXPECTED_RENDERABLE = 30

requires_corpus = pytest.mark.skipif(
    not CORPUS.is_dir() or not (LEGACY_SRC / "remarkable_spec").is_dir(),
    reason=(
        f"needs the reference corpus at {CORPUS} and the legacy tree at {LEGACY_SRC}; "
        f"three of the thirty hashes are covered corpus-free in test_render_svg_document.py"
    ),
)


LEGACY_LOGGER = "rmscene"
"""The logger the legacy reader reconfigures at import time.

``src/remarkable_spec/formats/rm_file.py:31`` runs
``logging.getLogger("rmscene").setLevel(logging.ERROR)`` as an import side effect. This module
imports that reader -- deliberately, because reproducing legacy bytes requires the legacy parse
-- and pytest runs the whole workspace in one process, so the mutation outlives these tests and
lands on every suite collected after them. ``rmspec-formats`` pins the opposite property for its
own codec (that decoding leaves another library's logger alone), and two of its tests failed
whenever the random ordering put this file first. Global state a test reaches for is state that
test owns putting back.
"""


@pytest.fixture(autouse=True)
def _restore_the_legacy_logger_level() -> Iterator[None]:
    """Undo the legacy reader's import-time logger mutation after every test here."""
    logger = logging.getLogger(LEGACY_LOGGER)
    before = logger.level
    try:
        yield
    finally:
        logger.setLevel(before)


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


def _legacy_module(name: str, /) -> ModuleType:
    """Import one legacy module, undoing its process-wide logging side effect.

    Importing anything under ``remarkable_spec`` runs
    ``src/remarkable_spec/formats/rm_file.py``, whose line 31 is a bare
    ``logging.getLogger("rmscene").setLevel(logging.ERROR)`` at module scope. That is
    exactly the defect ``rmspec.formats.scene_codec`` was written to avoid -- its docstring
    argues that "a library that reconfigures its host application's logging is a library
    that breaks its host's logging", and two tests in
    ``packages/rmspec-formats/tests/test_formats_scene_codec.py`` assert the ``rmscene``
    level is still ``NOTSET``.

    Those two tests failed whenever this module loaded first in the same process. It went
    unnoticed because every mise task runs ``pytest -n auto``, and xdist put the two
    packages in different workers; the contamination only appears in a single-process run.
    The legacy tree is excluded from lint and scheduled for deletion in step 7, so the fix
    belongs here, at the one place that pulls it in, rather than in a file with a deletion
    date.

    Parameters
    ----------
    name
        Dotted module path under ``remarkable_spec``.

    Returns
    -------
    ModuleType
        The imported module. Attribute access on it is untyped, which is the honest shape:
        the legacy tree ships no annotations and is loaded by name.
    """
    if str(LEGACY_SRC) not in sys.path:
        sys.path.insert(0, str(LEGACY_SRC))
    parser_logger = logging.getLogger("rmscene")
    restore = parser_logger.level
    try:
        return importlib.import_module(name)
    finally:
        parser_logger.setLevel(restore)


def _parse_layers(path: Path) -> list[LegacyLayer]:
    """Read one scene file with the reader that produced the manifest."""
    reader = _legacy_module("remarkable_spec.formats.rm_file")
    return list(reader.parse_rm_file(path))


def _to_page(legacy_layers: list[LegacyLayer]) -> Page:
    """Map the legacy reader's output onto domain values.

    Field for field, with no reinterpretation: this exists because the differential compares a
    *render*, so the parse on both sides has to be the same one.
    """
    return Page(
        page_id=PageId(uuid=ZERO_PAGE_UUID),
        index=0,
        content=PageContent(
            layers=tuple(
                Layer(
                    name=legacy_layer.name,
                    visible=legacy_layer.visible,
                    strokes=tuple(
                        Stroke(
                            pen=PenType(int(legacy_stroke.pen_type)),
                            color=PenColor(int(legacy_stroke.color)),
                            thickness_scale=legacy_stroke.thickness_scale,
                            points=tuple(
                                Point(
                                    x=sample.x,
                                    y=sample.y,
                                    speed=sample.speed,
                                    direction=sample.direction,
                                    width=sample.width,
                                    pressure=sample.pressure,
                                )
                                for sample in legacy_stroke.points
                            ),
                            starting_length=legacy_stroke.starting_length,
                        )
                        for legacy_stroke in legacy_layer.strokes
                    ),
                    text_blocks=tuple(_text_block(block) for block in legacy_layer.text_blocks),
                )
                for legacy_layer in legacy_layers
            )
        ),
    )


def _text_block(block: LegacyTextBlock) -> TextBlock:
    """Map one legacy text block onto the domain value."""
    return TextBlock(pos_x=block.pos_x, pos_y=block.pos_y, width=block.width, text=block.text)


def _entries() -> Iterator[Entry]:
    rows = _manifest()
    for path in _corpus_files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
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

    unknown = [
        path.name
        for path in non_empty
        if hashlib.sha256(path.read_bytes()).hexdigest() not in rows
    ]
    assert not unknown, f"corpus files with no manifest entry: {unknown}"
    assert len(ENTRIES) == EXPECTED_RENDERABLE


@requires_corpus
def test_every_zero_byte_stub_is_the_legacy_message_less_eof_error() -> None:
    """Finding 1, at corpus scale: sixty-two files, sixty-two bare ``EOFError``s.

    Pinned as the *defect*, not as desired behaviour. ``rmspec-formats`` owes a typed outcome
    here -- an empty stub decodes to ``PageContent(layers=(), defects=())``, which is readable
    and blank and distinct from a truncated file -- and ``test_render_empty_stub.py`` asserts
    the render side of that distinction. This test is the record of what the loop used to do
    to two thirds of a real document.
    """
    stubs = [path for path in _corpus_files() if path.stat().st_size == 0]
    assert len(stubs) == EXPECTED_EMPTY_STUBS

    failures: list[str] = []
    for path in stubs:
        try:
            _parse_layers(path)
        except EOFError as exc:
            if str(exc):
                failures.append(f"{path.name}: EOFError carried a message: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
        else:
            failures.append(f"{path.name}: parsed without raising")
    assert not failures, "\n".join(failures)


@requires_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=[entry.rm_sha256[:12] for entry in ENTRIES])
def test_the_parse_matches_the_manifest_counts(entry: Entry) -> None:
    """Asserted before any hash, so a codec regression is attributed to the codec."""
    layers = _parse_layers(entry.path)
    assert len(layers) == entry.layers
    assert sum(len(layer.strokes) for layer in layers) == entry.strokes


@requires_corpus
@pytest.mark.parametrize("entry", ENTRIES, ids=[entry.rm_sha256[:12] for entry in ENTRIES])
def test_the_rendered_svg_is_byte_identical_to_the_legacy_output(entry: Entry) -> None:
    """The hardest gate. The comparison is a hash of bytes and is never relaxed."""
    page = _to_page(_parse_layers(entry.path))
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
    """Explain a mismatch precisely, because a hash pair explains nothing.

    Reports the byte-length delta first -- a constant 39 names the XML declaration, 1 names a
    trailing newline, anything larger names geometry -- then the first differing byte offset
    against the legacy output regenerated on the spot, then three classifiers for the known
    suspects.
    """
    legacy = _legacy_svg(entry)
    delta = len(raw) - entry.svg_bytes
    offset = next(
        (index for index in range(min(len(legacy), len(raw))) if legacy[index] != raw[index]),
        min(len(legacy), len(raw)),
    )
    window = slice(max(0, offset - 80), offset + 80)
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
    )
    return "\n".join(
        [
            (
                f"{entry.rm_sha256[:12]} rendered {len(raw)} bytes, "
                f"manifest says {entry.svg_bytes} (delta {delta:+d})"
            ),
            f"screen={entry.screen} layers={entry.layers} strokes={entry.strokes}",
            f"contains a PenColor.HIGHLIGHT stroke: {highlight}",
            f"visible non-empty text blocks: {text_blocks}",
            f"first difference at byte {offset}",
            f"  legacy: {legacy[window]!r}",
            f"  new   : {raw[window]!r}",
        ]
    )


def _legacy_svg(entry: Entry) -> bytes:
    """Re-render one entry with the legacy code, for the diff a hash pair cannot give."""
    engine = _legacy_module("remarkable_spec.render.engine")
    legacy_page_model = _legacy_module("remarkable_spec.models.page")
    screens = _legacy_module("remarkable_spec.models.screen")
    layers = _parse_layers(entry.path)
    legacy_screen = screens.PAPER_PRO_SCREEN if entry.screen == "1620x2160" else screens.RM2_SCREEN
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory) / "legacy.svg"
        engine.SVGRenderer().render_page(
            legacy_page_model.Page(uuid=UUID(int=0), layers=layers),
            destination,
            screen=legacy_screen,
            thickness=LEGACY_STYLE.thickness_scale,
        )
        return destination.read_bytes()


@requires_corpus
def test_no_oracle_entry_carries_a_highlight_stroke_or_typed_text() -> None:
    """The two divergences that *could* have broken byte-exactness, measured rather than assumed.

    The legacy ``RM_PALETTE`` had no entry for ``PenColor.HIGHLIGHT`` (9) and its ``get_rgb``
    fell back to black, while the domain's ``EXPORT_PALETTE`` is validated total and maps it to
    ``(251, 247, 25)``; and the legacy renderer drew no typed text at all. Either would move
    hashes if the corpus contained one. It contains neither -- every stroke in all thirty pages
    is ``PenColor.BLACK`` -- so ``EXPORT_PALETTE`` is used unmodified and text is drawn, with no
    manifest regeneration and no re-introduced fallback.
    """
    colours: set[int] = set()
    text_blocks = 0
    for entry in ENTRIES:
        page = _to_page(_parse_layers(entry.path))
        content = page.content
        assert content is not None
        for layer in content.layers:
            colours.update(int(stroke.color) for stroke in layer.strokes)
            text_blocks += sum(1 for block in layer.text_blocks if block.text.strip())

    assert PenColor.HIGHLIGHT.value not in colours, (
        "a colour-9 stroke would render black in the oracle and yellow here; inject a "
        "legacy-replica palette into this harness rather than regenerating the manifest"
    )
    assert text_blocks == 0, (
        "a visible text block would make byte-exactness and the port's anti-vanishing rule "
        "mutually exclusive for that entry"
    )


@requires_corpus
def test_the_mismatch_diagnostic_names_the_delta_and_the_first_differing_byte() -> None:
    """A precise failure is the deliverable, so the failure path is exercised on purpose.

    ``_diagnose`` only runs when a hash moves, which is exactly when nobody wants to discover
    it raises. Fed a deliberately corrupted rendering, it must report the byte delta, the
    offset and the two classifiers rather than blowing up.
    """
    entry = ENTRIES[0]
    page = _to_page(_parse_layers(entry.path))
    corrupted = b"<svg>not what the oracle says</svg>"
    report = _diagnose(entry, corrupted, page)

    assert entry.rm_sha256[:12] in report
    assert f"manifest says {entry.svg_bytes}" in report
    assert "first difference at byte" in report
    assert "PenColor.HIGHLIGHT" in report
    assert "text blocks" in report
