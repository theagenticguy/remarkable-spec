"""The recorded legacy behaviour this relocation is held to, where it can be checked.

``tests/fixtures/render-differential-manifest.json`` was produced by the legacy tree at
commit 14f1960 over one device's backup. That tree was deleted on 2026-08-30, which changed
nothing here: this module never imported it. It has always compared the relocated codec against
the *recording*, never against a running second implementation, so "differential" in the file
name means a differential against a frozen artifact. The manifest is now the only witness to
what the legacy reader read, and re-recording it is impossible -- which is the argument for
never regenerating it to make a failure go away.

It is keyed by ``rm_sha256`` and commits no bytes -- "the source files are a personal backup
outside the repo and are not committed" -- so two kinds of assertion live here:

1. **Manifest invariants, always checked.** Cheap, and they pin the facts this package was
   built against: that the recording had no failures, and what the 62-of-92 empty-stub
   class means for the codec.
2. **Corpus assertions, gated on ``RMSPEC_CORPUS``.** For every ``.rm`` file under that
   directory, the relocated decode must reproduce the legacy ``layers`` and ``strokes``
   counts recorded for those exact bytes. Those two numbers are the only per-page facts
   the manifest holds -- there is no recorded per-page decode digest, so inventing one
   here and recording it from the new code would prove nothing.

The gate skips loudly when the corpus is absent, and never passes silently: a skip names
the environment variable, and the manifest half fails on its own if the recording is not
what this package was written against.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest

from rmspec.domain.errors import RmspecError
from rmspec.domain.models import PageContent
from rmspec.formats import SceneCodec, fingerprint_bytes

REPO = pathlib.Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO / "tests" / "fixtures" / "render-differential-manifest.json"
CORPUS_ENV = "RMSPEC_CORPUS"
KNOWN_SCREENS = {"1620x2160", "1404x1872"}


def manifest() -> dict[str, Any]:
    """Read the recorded legacy manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def entries() -> dict[str, dict[str, Any]]:
    """Return the recorded entries, keyed by the sha256 of the page's bytes."""
    return {entry["rm_sha256"]: entry for entry in manifest()["entries"]}


#: Where a personal xochitl backup conventionally lives on a developer machine.
#: Checked when RMSPEC_CORPUS is unset, so the differential oracle runs by default
#: for anyone who has the corpus rather than only for someone who remembers the
#: environment variable. A gate that silently opts itself out is the failure mode
#: this project has already hit once, with a coverage floor that measured 18
#: statements and reported 100%.
DEFAULT_CORPUS_LOCATIONS = (
    pathlib.Path.home() / "remarkable",
    pathlib.Path.home() / "remarkable-backup" / "xochitl",
)


def corpus_root() -> pathlib.Path | None:
    """Resolve the corpus location, or None when this machine has no copy."""
    location = os.environ.get(CORPUS_ENV)
    if location:
        return pathlib.Path(location)
    return next((c for c in DEFAULT_CORPUS_LOCATIONS if c.is_dir()), None)


def corpus_files() -> list[pathlib.Path]:
    """Return every ``.rm`` file in the corpus, or skip loudly when there is none."""
    root = corpus_root()
    if root is None:
        pytest.skip(
            f"no reference corpus on this machine. It is a personal device backup kept "
            f"outside the repository, so CI legitimately lacks it. Looked for "
            f"{CORPUS_ENV} and then "
            f"{', '.join(str(c) for c in DEFAULT_CORPUS_LOCATIONS)}."
        )
    if not root.is_dir():
        pytest.skip(f"corpus path {root} is not a directory, so no corpus can be read.")
    files = sorted(root.rglob("*.rm"))
    if not files:
        pytest.skip(f"corpus path {root} holds no .rm files.")
    return files


# ─────────────────────────── manifest invariants ───────────────────────────


def test_the_manifest_is_present_and_recorded_no_failures():
    recorded = manifest()

    assert recorded["failures"] == [], "the recording itself failed; its counts are not an oracle"
    assert recorded["count"] == len(entries())


def test_every_recorded_entry_carries_the_two_facts_this_package_can_reproduce():
    for digest, entry in entries().items():
        assert len(digest) == 64
        assert entry["layers"] >= 1
        assert entry["strokes"] >= 0
        assert entry["screen"] in KNOWN_SCREENS


def test_the_recorded_screen_split_is_not_this_package_to_reproduce():
    """Finding 2, stated where it can be seen rather than silently skipped.

    ``detect_screen`` classified 23 of the 30 renderable pages as Paper Pro and 7 as
    reMarkable 2 from one device's backup. Nothing in this package reads or reproduces
    that: the geometry is not a fact of the scene bytes, so no ``DocumentRepository`` or
    ``PageCodec`` method returns one. The recorded value per entry is what the render
    oracle replays.
    """
    screens = {entry["screen"] for entry in entries().values()}

    assert screens == KNOWN_SCREENS, "one backup, two geometries -- see the return notes"


def test_the_empty_stub_class_is_what_this_codec_implements():
    stub_class = manifest()["empty_stub_class"]

    assert stub_class["count"] == 62
    assert stub_class["of_total"] == 92
    assert SceneCodec().decode_page(b"", "any-page") == PageContent(), (
        "the required new behaviour: a stub is a page with no ink, not a parse failure"
    )


# ─────────────────────────── the corpus ───────────────────────────


def test_the_relocated_decode_reproduces_the_recorded_layer_and_stroke_counts():
    recorded = entries()
    codec = SceneCodec()
    matched = 0
    mismatches: list[str] = []

    for path in corpus_files():
        raw = path.read_bytes()
        entry = recorded.get(fingerprint_bytes(raw))
        if entry is None:
            continue
        matched += 1
        content = codec.decode_page(raw, path.name)
        strokes = sum(len(layer.strokes) for layer in content.layers)
        if (len(content.layers), strokes) != (entry["layers"], entry["strokes"]):
            mismatches.append(
                f"{path.name}: recorded {entry['layers']} layers / {entry['strokes']} strokes, "
                f"decoded {len(content.layers)} / {strokes}"
            )

    expected = manifest()["count"]
    assert matched == expected, (
        f"{CORPUS_ENV} holds .rm files, but only {matched} of the manifest's {expected} "
        f"recorded digests appear in it. `matched > 0` passes on a single-file corpus and "
        f"would let a partial or substituted backup stand in for the recorded one, which "
        f"proves nothing about the pages that are missing."
    )
    report = "\n".join(mismatches)
    assert mismatches == [], f"the relocated decode changed what the legacy reader read:\n{report}"


def test_every_zero_byte_file_in_the_corpus_decodes_as_a_blank_page():
    """The 62-of-92 finding, against the real files rather than a synthetic stub."""
    codec = SceneCodec()
    stubs = [path for path in corpus_files() if path.stat().st_size == 0]

    for path in stubs:
        assert codec.decode_page(path.read_bytes(), path.name) == PageContent()

    assert stubs, "this corpus has no zero-byte stubs, so the finding cannot be checked on it"


def test_no_corpus_page_fails_to_decode():
    """Nothing in a real backup may raise out of the codec.

    Only a domain error is caught and reported. Anything else -- a parser exception, a
    pydantic ``ValidationError``, a ``struct.error`` -- propagates and fails this test with
    its own traceback, which is the stronger statement: the codec is documented to let
    none of them out, so there is nothing here to tolerate them.
    """
    codec = SceneCodec()
    refused: list[str] = []

    for path in corpus_files():
        try:
            codec.decode_page(path.read_bytes(), path.name)
        except RmspecError as err:
            refused.append(f"{path.name}: {type(err).__name__}: {err}")

    report = "\n".join(refused)
    assert refused == [], f"pages the relocated codec refuses:\n{report}"
