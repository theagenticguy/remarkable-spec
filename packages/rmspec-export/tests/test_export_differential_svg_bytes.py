"""The export half of the recorded SVG oracle: bytes in, identical bytes on disk.

What this package owns of the oracle, and what it does not
--------------------------------------------------------
``tests/fixtures/render-differential-manifest.json`` records, for 30 real ``.rm`` files, the
sha256 and byte length of the SVG the legacy code produced. Those bytes are produced by the
*render* slice; this package's entire contribution is the last step, and it is one sentence:
``sha256`` of the committed file equals ``sha256`` of the payload handed to the sink.

So this module runs the pipeline end to end and reports the two halves separately. A payload
whose hash already differs from the manifest is a render regression; a payload that matches and
a file that does not is an export regression. Reporting one number for both is what would make
a failure read as a renderer bug when the sink appended a byte, and the byte shape makes that
concrete: the legacy writer emitted ``<?xml version='1.0' encoding='utf-8'?>`` with *single*
quotes followed by one newline, no BOM, and **no** trailing newline. A sink that appended one
would shift every entry by exactly one byte, and ``svg_bytes`` is asserted before ``svg_sha256``
so the report says "+1 byte" rather than "hash mismatch".

Recorded oracle, not a differential
-----------------------------------
Until 2026-08-30 the SVG string came from one of two producers resolved at run time: the new
render slice if it ever exposed one, otherwise the legacy ``src/remarkable_spec`` tree. That
tree has been deleted, so no second implementation stands behind the 30 hashes any more. They
are unchanged and still compared byte for byte, but a green run here is a **regression pin** on
``rmspec.formats`` plus ``rmspec.render``, not evidence that an independent implementation
agrees. The agreement was verified on 2026-08-30, with the legacy tree still present: all 30
entries reproduced byte-identically through the legacy parse and render, and then again with
the parse and render re-pointed at the new slices. See
``packages/rmspec-render/tests/test_render_differential.py`` for the fuller account.

The upside of the deletion is that this module stopped being able to skip for lack of a
producer. ``_render_producer`` is now implemented rather than returning ``None``, so the only
remaining reason to skip is a machine without the corpus -- the 30 source files are a personal
backup outside the repository (``~/remarkable``) and the rendered SVGs are not committed either,
which is why the manifest is keyed by content hash. That skip is never weakened into an
``xfail`` and the comparison is never loosened, because a precise skip is worth more than a
green test that compares something weaker.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import pytest
from export_support import artifact_name

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    RM2_SCREEN,
    Page,
    PageId,
    ScreenSpec,
)
from rmspec.domain.ports.export import ArtifactMedia
from rmspec.domain.ports.render import RenderStyle, TextStyle
from rmspec.export.sink import FilesystemArtifactSink
from rmspec.formats import SceneCodec, fingerprint_bytes
from rmspec.render import (
    LEGACY_MIN_PADDING_MM,
    LEGACY_THICKNESS_SCALE,
    SVG_RENDERER_REVISION,
    SvgPageRenderer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO = pathlib.Path(__file__).resolve().parents[3]
MANIFEST = REPO / "tests" / "fixtures" / "render-differential-manifest.json"

CORPUS = pathlib.Path.home() / "remarkable"

#: Render parameters the manifest records under ``render_params``. Changing any of them
#: invalidates every hash, so ``test_the_render_policy_still_matches_the_recorded_parameters``
#: asserts these against the recording itself rather than trusting the comment.
THICKNESS = LEGACY_THICKNESS_SCALE
PAGE_UUID = str(UUID(int=0))

#: The screen each entry was rendered at. The legacy code called ``detect_screen(layers)``; the
#: domain deliberately ships no such function -- geometry is not a fact of the scene bytes -- so
#: the recorded answer is replayed per entry instead of re-derived.
SCREENS: dict[str, ScreenSpec] = {
    "1620x2160": PAPER_PRO_SCREEN,
    "1404x1872": RM2_SCREEN,
}

#: Text policy. The corpus carries no visible typed text -- asserted over all 30 entries in
#: ``packages/rmspec-render/tests/test_render_differential.py`` -- so nothing here depends on
#: these values; ``RenderStyle`` requires them and offers no default to drift.
TEXT_STYLE = TextStyle(family="sans-serif", size_px=32.0, line_height=1.2)

#: Exactly the parameter set the manifest records.
LEGACY_STYLE = RenderStyle(
    thickness_scale=THICKNESS,
    min_padding_mm=LEGACY_MIN_PADDING_MM,
    text=TEXT_STYLE,
    renderer_revision=SVG_RENDERER_REVISION,
)


class SvgProducer(Protocol):
    """Renders one ``.rm`` file to the SVG string the manifest hashed."""

    def __call__(self, path: pathlib.Path, *, screen: ScreenSpec) -> str:
        """Render the file.

        Parameters
        ----------
        path
            Location of a non-empty ``.rm`` file.
        screen
            The geometry the manifest recorded for these bytes.

        Returns
        -------
        str
            SVG document text, exactly as the writer would have put it on disk.
        """
        ...


def _render_producer() -> SvgProducer:
    """Build the producer: decode with ``rmspec.formats``, render with ``rmspec.render``.

    This function used to return ``None`` on purpose, and the argument for that is worth
    recording because it no longer holds. Going from a ``.rm`` file to the domain page
    :class:`rmspec.render.SvgPageRenderer` renders needs the *formats* slice's parser, and the
    dependency table gives this package ``rmspec-domain`` and ``rmspec-render`` only -- so the
    module reached for the legacy tree instead, and skipped once that tree was gone. The
    composition was left to hold transitively: ``rmspec-render``'s oracle asserted new bytes
    equal legacy bytes, and this module asserted the committed file equals the payload it was
    handed.

    Deleting the legacy tree took the first half of that composition away. The choice became a
    permanently-skipping test or a cross-slice import in a *test*, and the second is both
    honest and already the workspace's practice: the edge that is enforced is the one
    ``tests/architecture/test_dependency_direction.py`` checks, it scans ``src`` only, and
    ``rmspec-cli``'s suite composes seven slices the same way. Nothing under
    ``packages/rmspec-export/src`` imports ``rmspec.formats``, and nothing may.

    Returns
    -------
    SvgProducer
        A producer that decodes and renders one entry at the manifest's parameters.
    """
    codec = SceneCodec()
    renderer = SvgPageRenderer()

    def render(path: pathlib.Path, *, screen: ScreenSpec) -> str:
        content = codec.decode_page(path.read_bytes(), path.name)
        page = Page(page_id=PageId(uuid=PAGE_UUID), index=0, content=content)
        rendered = renderer.render(
            page,
            screen=screen,
            palette=EXPORT_PALETTE,
            style=LEGACY_STYLE,
        )
        return rendered.svg

    return render


def _manifest() -> dict[str, dict[str, object]]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {str(entry["rm_sha256"]): entry for entry in document["entries"]}


def _corpus_files() -> Iterator[tuple[str, pathlib.Path, bytes]]:
    for path in sorted(CORPUS.rglob("*.rm")):
        raw = path.read_bytes()
        yield fingerprint_bytes(raw), path, raw


pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def oracle() -> tuple[SvgProducer, dict[str, dict[str, object]]]:
    """Resolve the producer and the manifest, or skip with the reason spelled out.

    The producer is always available now that it is built from the workspace's own slices, so
    the only reasons left to skip are a missing manifest and a machine without the corpus.
    There used to be a third -- "no SVG producer is available" -- which the legacy tree's
    deletion turned into a condition that could never be false while also being the one thing
    that would silence the comparison. It is gone rather than left standing.

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
    return _render_producer(), _manifest()


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


def test_the_render_policy_still_matches_the_recorded_parameters() -> None:
    """The parameters the 30 hashes encode, checked against the recording, corpus or not.

    Cheap, and it runs on a machine with no backup -- which matters more than it did. While the
    legacy tree existed, a drifted thickness or page identity would have been caught by the
    comparison itself, because the reference bytes were regenerated from the same parameters.
    Now the manifest is the only witness, so the parameters are asserted against it directly
    instead of living in a comment that a reader has to take on trust.
    """
    recorded = cast("dict[str, object]", json.loads(MANIFEST.read_text(encoding="utf-8")))
    params = cast("dict[str, object]", recorded["render_params"])

    assert params["thickness"] == LEGACY_STYLE.thickness_scale
    assert params["page_uuid"] == "UUID(int=0)"
    assert set(SCREENS) == {"1620x2160", "1404x1872"}


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
        payload = producer(path, screen=SCREENS[cast("str", entry["screen"])]).encode("utf-8")
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
