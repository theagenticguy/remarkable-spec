"""The export half of the SVG differential oracle: bytes in, identical bytes on disk.

What this package owns of the oracle, and what it does not
--------------------------------------------------------
``tests/fixtures/render-differential-manifest.json`` records, for 30 real ``.rm`` files, the
sha256 and byte length of the SVG the legacy code produced. Those bytes are produced by the
*render* slice; this package's entire contribution is the last step, and it is one sentence:
``sha256`` of the committed file equals ``sha256`` of the payload handed to the sink.

So this module runs the oracle end to end and reports the two halves separately. A payload
whose hash already differs from the manifest is a render regression; a payload that matches and
a file that does not is an export regression. Reporting one number for both is what would make
a failure read as a renderer bug when the sink appended a byte, and the byte shape makes that
concrete: the legacy writer emitted ``<?xml version='1.0' encoding='utf-8'?>`` with *single*
quotes followed by one newline, no BOM, and **no** trailing newline. A sink that appended one
would shift every entry by exactly one byte, and ``svg_bytes`` is asserted before ``svg_sha256``
so the report says "+1 byte" rather than "hash mismatch".

Why it may skip, and why that is stated loudly rather than relaxed
----------------------------------------------------------------
The 30 source files are a personal backup outside the repository (``~/remarkable``), and the
rendered SVGs are not committed either -- the manifest is keyed by content hash for exactly that
reason. The producer of the SVG string is resolved at run time: the new
:mod:`rmspec.render` slice when it exposes one, otherwise the legacy ``src/remarkable_spec``
tree while it still exists. When neither is available the test skips with the reason spelled
out. It is never weakened into an ``xfail`` and the comparison is never loosened, because a
precise skip is worth more than a green test that compares something weaker.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import pathlib
import sys
import tempfile
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import pytest
from export_support import artifact_name

from rmspec.domain.ports.export import ArtifactMedia
from rmspec.export.sink import FilesystemArtifactSink

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = REPO / "tests" / "fixtures" / "render-differential-manifest.json"
LEGACY_SOURCE = REPO / "src"

CORPUS = pathlib.Path.home() / "remarkable"

LEGACY_PARSER_LOGGER = "rmscene"
"""The logger the legacy reader raises to ERROR at import time, process-wide.

Named rather than inlined so the two differential suites that pull in the legacy tree
-- this one and ``packages/rmspec-render/tests/test_render_differential.py`` -- spell it
the same way, and so a grep for it finds every site that has to put it back.
"""

#: Render parameters the manifest records. Changing any of them invalidates every hash.
THICKNESS = 1.5
PAGE_UUID = UUID(int=0)


class SvgProducer(Protocol):
    """Renders one ``.rm`` file to the SVG string the manifest hashed."""

    def __call__(self, path: pathlib.Path) -> str:
        """Render the file.

        Parameters
        ----------
        path
            Location of a non-empty ``.rm`` file.

        Returns
        -------
        str
            SVG document text, exactly as the writer would have put it on disk.
        """
        ...


def _legacy_producer() -> SvgProducer | None:
    """Build a producer from the legacy tree, while it still exists.

    Returns
    -------
    SvgProducer | None
        The producer, or ``None`` when the legacy tree has been deleted.
    """
    if not (LEGACY_SOURCE / "remarkable_spec" / "render" / "engine.py").is_file():
        return None
    if str(LEGACY_SOURCE) not in sys.path:
        sys.path.insert(0, str(LEGACY_SOURCE))
    # src/remarkable_spec/formats/rm_file.py:31 runs
    # `logging.getLogger("rmscene").setLevel(logging.ERROR)` as an import side effect, so
    # importing it here mutates the level for the rest of the process. Two tests in
    # packages/rmspec-formats/tests/test_formats_scene_codec.py assert that level is still
    # NOTSET -- rmspec.formats.scene_codec exists partly to argue that a library which
    # reconfigures its host's logging is broken -- and both failed whenever this module ran
    # first in the same process. It went unseen because every mise task passes `-n auto` and
    # xdist put the two packages in different workers; only a single-process run shows it.
    # The sibling at packages/rmspec-render/tests/test_render_differential.py already
    # restores the level and this file was the one that did not. The legacy tree is excluded
    # from lint and is deleted in step 7, so the fix belongs at the call site rather than in
    # a file with a deletion date.
    parser_logger = logging.getLogger(LEGACY_PARSER_LOGGER)
    restore = parser_logger.level
    try:
        rm_file = importlib.import_module("remarkable_spec.formats.rm_file")
        page_module = importlib.import_module("remarkable_spec.models.page")
        screen_module = importlib.import_module("remarkable_spec.models.screen")
        engine = importlib.import_module("remarkable_spec.render.engine")
        palette_module = importlib.import_module("remarkable_spec.render.palette")
    except ImportError:
        return None
    finally:
        parser_logger.setLevel(restore)
    parse_rm_file = rm_file.parse_rm_file
    page_type = page_module.Page
    detect_screen = screen_module.detect_screen
    renderer_type = engine.SVGRenderer
    export_palette = palette_module.EXPORT_PALETTE

    def render(path: pathlib.Path) -> str:
        layers = parse_rm_file(path)
        with tempfile.TemporaryDirectory() as raw:
            output = pathlib.Path(raw) / "page.svg"
            renderer_type().render_page(
                page=page_type(uuid=PAGE_UUID, layers=layers),
                output=output,
                palette=export_palette,
                screen=detect_screen(layers),
                thickness=THICKNESS,
            )
            return output.read_bytes().decode("utf-8")

    return render


def _render_producer() -> SvgProducer | None:
    """Build a producer from the new render slice, if this package could reach one.

    It cannot, and that is a dependency fact rather than unfinished work. Going from a ``.rm``
    file to the domain page :class:`rmspec.render.SvgPageRenderer` renders needs the *formats*
    slice's parser, and the dependency table gives this package ``rmspec-domain`` and
    ``rmspec-render`` only. Wiring it here -- even lazily, even in a test -- would be this
    package importing ``rmspec.formats``.

    Nothing is lost, because the two halves compose. ``rmspec-render``'s own oracle asserts
    ``new render bytes == legacy bytes`` for all 30 entries, and this module asserts
    ``committed file == the payload it was handed``, with the payload's hash checked against the
    manifest first. Transitively the committed file equals the new renderer's bytes, and each
    half fails with the diagnosis that belongs to it instead of one hash mismatch that could be
    either. The hook stays because a producer this package *may* reach -- a domain-typed page
    fixture, or the app slice's bridge once it exists -- would plug in here.

    Returns
    -------
    SvgProducer | None
        Always ``None``.
    """
    return None


def _producer() -> SvgProducer | None:
    return _render_producer() or _legacy_producer()


def _manifest() -> dict[str, dict[str, object]]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {str(entry["rm_sha256"]): entry for entry in document["entries"]}


def _corpus_files() -> Iterator[tuple[str, pathlib.Path, bytes]]:
    for path in sorted(CORPUS.rglob("*.rm")):
        raw = path.read_bytes()
        yield hashlib.sha256(raw).hexdigest(), path, raw


pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def oracle() -> tuple[SvgProducer, dict[str, dict[str, object]]]:
    """Resolve the producer and the manifest, or skip with the reason spelled out.

    Returns
    -------
    tuple[SvgProducer, dict[str, dict[str, object]]]
        The producer and the manifest keyed by ``rm_sha256``.
    """
    if not MANIFEST.is_file():
        pytest.skip(f"the differential manifest is missing from {MANIFEST}")
    if not CORPUS.is_dir():
        pytest.skip(
            f"the reference corpus is not on this machine ({CORPUS}); it is a personal backup "
            "and is deliberately not committed, so the oracle cannot run here"
        )
    producer = _producer()
    if producer is None:
        pytest.skip(
            "no SVG producer is available: rmspec.render exposes none yet and the legacy "
            "src/remarkable_spec tree is gone. Point _render_producer() at the render slice's "
            "renderer -- until then the SVG half of the oracle is unverified, which is a gap "
            "and not a pass"
        )
    return producer, _manifest()


def test_the_corpus_is_two_thirds_empty_stubs() -> None:
    if not CORPUS.is_dir():
        pytest.skip(f"the reference corpus is not on this machine ({CORPUS})")
    files = list(_corpus_files())
    empty = [sha for sha, _path, raw in files if len(raw) == 0]
    assert len(files) > 0
    assert len(empty) > len(files) // 2, (
        "the corpus is expected to be mostly zero-byte stubs -- the unannotated pages of a "
        "PDF-backed document. If that changed, the empty-stub findings need re-measuring"
    )


def test_every_manifest_entry_reproduces_byte_for_byte(
    oracle: tuple[SvgProducer, dict[str, dict[str, object]]],
    tmp_path: pathlib.Path,
) -> None:
    producer, manifest = oracle
    sink = FilesystemArtifactSink(destination=tmp_path, overwrite=True, dry_run=False)
    render_mismatches: list[str] = []
    export_mismatches: list[str] = []
    checked = 0
    for rm_sha256, path, raw in _corpus_files():
        entry = manifest.get(rm_sha256)
        if entry is None or len(raw) == 0:
            continue
        checked += 1
        payload = producer(path).encode("utf-8")
        expected_bytes = cast("int", entry["svg_bytes"])
        expected_hash = cast("str", entry["svg_sha256"])
        payload_hash = hashlib.sha256(payload).hexdigest()
        if len(payload) != expected_bytes or payload_hash != expected_hash:
            # Length before hash: a delta of exactly 39 is the missing XML declaration, +/-1 is a
            # trailing newline, and a large delta is padding or numeric formatting. The hash
            # alone throws that diagnosis away.
            render_mismatches.append(
                f"{rm_sha256[:12]} {path.name}: payload {len(payload)} bytes "
                f"(expected {expected_bytes}, delta {len(payload) - expected_bytes:+d}), "
                f"sha256 {payload_hash[:12]} expected {expected_hash[:12]}"
            )
            continue
        receipt = sink.write(artifact_name(rm_sha256[:16]), payload, media=ArtifactMedia.SVG)
        landed = tmp_path / f"{rm_sha256[:16]}.svg"
        on_disk = landed.read_bytes()
        on_disk_hash = hashlib.sha256(on_disk).hexdigest()
        if on_disk_hash != expected_hash or len(on_disk) != expected_bytes:
            first_difference = next(
                (
                    index
                    for index, (left, right) in enumerate(zip(payload, on_disk, strict=False))
                    if left != right
                ),
                min(len(payload), len(on_disk)),
            )
            export_mismatches.append(
                f"{rm_sha256[:12]} {path.name}: the sink changed the bytes -- file "
                f"{len(on_disk)} bytes vs payload {len(payload)}, first difference at offset "
                f"{first_difference}, sha256 {on_disk_hash[:12]} expected {expected_hash[:12]}"
            )
        assert receipt.byte_count == len(payload)
    assert checked == len(manifest), (
        f"only {checked} of {len(manifest)} manifest entries were found in the corpus; the "
        "oracle is keyed by content hash, so a missing file means the backup has changed"
    )
    assert render_mismatches == [], (
        "RENDER regression -- these payloads differ before the sink is reached:\n"
        + "\n".join(render_mismatches)
    )
    assert export_mismatches == [], (
        "EXPORT regression -- these payloads matched and the committed file did not:\n"
        + "\n".join(export_mismatches)
    )


def test_the_sink_preserves_the_exact_legacy_svg_byte_shape(tmp_path: pathlib.Path) -> None:
    # Runs everywhere, corpus or not. The prologue's single quotes, the one newline after it and
    # the absence of a trailing newline are the three facts the 30 hashes depend on, measured from
    # the legacy writer's own output.
    payload = (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-30.00 -30.00 569.34 739.13" '
        b'width="569.34" height="739.13">\n  <rect x="1" y="1" width="10" height="10" />\n</svg>'
    )
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert not payload.endswith(b"\n")
    sink = FilesystemArtifactSink(destination=tmp_path, overwrite=False, dry_run=False)
    sink.write(artifact_name("page-000"), payload, media=ArtifactMedia.SVG)
    on_disk = (tmp_path / "page-000.svg").read_bytes()
    assert on_disk == payload
    assert hashlib.sha256(on_disk).hexdigest() == hashlib.sha256(payload).hexdigest()
    assert len(on_disk) == len(payload)
