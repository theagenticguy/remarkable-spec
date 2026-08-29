"""The filesystem sink: verbatim bytes, one suffix source, atomicity, and the errno table."""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from export_support import (
    MemoryArtifactSink,
    artifact_name,
    check_sink,
    check_sink_refuses_a_second_write,
    sha256,
)
from hypothesis import given
from hypothesis import strategies as st

from rmspec.domain.errors import ArtifactWriteFailed, ArtifactWriteReason
from rmspec.domain.ports.export import ArtifactMedia, ArtifactName
from rmspec.export.sink import (
    SUFFIXES,
    TEMPORARY_PREFIX,
    TEMPORARY_SUFFIX,
    FilesystemArtifactSink,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

PAYLOADS: tuple[bytes, ...] = (
    b"plain ascii",
    b"<?xml version='1.0' encoding='utf-8'?>\n<svg/>",
    "astral \U0001f4d3 plane".encode(),
    b"crlf\r\nand\rlone\ncr",
    b"\x00\x01\x02trailing-nul\x00",
    b"%PDF-1.7\n%%EOF\n",
)


def _sink(
    destination: Path, *, overwrite: bool = False, dry_run: bool = False
) -> FilesystemArtifactSink:
    return FilesystemArtifactSink(
        destination=destination,
        overwrite=overwrite,
        dry_run=dry_run,
    )


def test_the_real_adapter_satisfies_the_contract(tmp_path: Path) -> None:
    check_sink(_sink(tmp_path / "out"), payloads=PAYLOADS)


def test_the_in_memory_double_satisfies_the_same_contract() -> None:
    check_sink(MemoryArtifactSink(), payloads=PAYLOADS)


def test_both_implementations_refuse_a_second_write_to_the_same_name(tmp_path: Path) -> None:
    # Both, now: the ALREADY_PRESENT clause used to be asserted against the filesystem adapter
    # only, which left an app-layer fake free to overwrite silently where production raises.
    check_sink_refuses_a_second_write(_sink(tmp_path / "out"))
    check_sink_refuses_a_second_write(MemoryArtifactSink(overwrite=False))


def test_the_in_memory_double_can_be_told_to_overwrite() -> None:
    sink = MemoryArtifactSink(overwrite=True)
    sink.write(artifact_name("p"), b"first", media=ArtifactMedia.SVG)
    sink.write(artifact_name("p"), b"second", media=ArtifactMedia.SVG)
    assert sink.written["p"] == (ArtifactMedia.SVG, b"second")


def _name_max(directory: Path) -> int:
    return os.pathconf(str(directory), "PC_NAME_MAX")


@pytest.mark.parametrize("media", list(ArtifactMedia))
def test_the_longest_name_the_filesystem_accepts_is_written(
    tmp_path: Path,
    media: ArtifactMedia,
) -> None:
    # The regression the injection-based errno table below could never catch: it raises
    # ENAMETOOLONG artificially, so it passed whether or not the sink could construct a legal
    # temporary of its own. While the temporary's name was derived from the artifact's, every
    # stem from 241 characters up failed here with NOT_WRITABLE and nothing was written, though
    # the legacy exporters' plain write_bytes accepted the same stem and the target name was a
    # legal 245. The ceiling is now the filesystem's alone.
    stem = "x" * (_name_max(tmp_path) - len(SUFFIXES[media]))
    payload = b"%PDF-1.7\n%%EOF\n"
    receipt = _sink(tmp_path).write(ArtifactName(value=stem), payload, media=media)
    landed = tmp_path / f"{stem}{SUFFIXES[media]}"
    assert landed.read_bytes() == payload
    assert receipt.byte_count == len(payload)
    assert receipt.committed is True


def test_one_character_past_the_filesystem_ceiling_is_not_writable(tmp_path: Path) -> None:
    # Typed, not a raw OSError. Path.exists swallows only the errnos meaning "not found", and on
    # 3.13 ENAMETOOLONG is not one of them, so the overwrite probe used to raise straight out of
    # write() -- an untyped failure crossing a port whose contract is ArtifactWriteFailed.
    stem = "x" * (_name_max(tmp_path) - len(SUFFIXES[ArtifactMedia.SVG]) + 1)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(ArtifactName(value=stem), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.NOT_WRITABLE
    assert caught.value.name == stem
    assert list(tmp_path.iterdir()) == [], "refused before a directory or a temporary is created"


def test_a_name_the_filesystem_refuses_is_typed_with_overwriting_on_too(tmp_path: Path) -> None:
    # The overwrite flag skips the ALREADY_PRESENT branch but not the probe, so both policies have
    # to reach the same typed failure. With overwriting on the write proceeds to the rename, which
    # is the other place ENAMETOOLONG can surface.
    stem = "x" * (_name_max(tmp_path) - len(SUFFIXES[ArtifactMedia.PNG]) + 1)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path, overwrite=True).write(
            ArtifactName(value=stem),
            b"x",
            media=ArtifactMedia.PNG,
        )
    assert caught.value.reason is ArtifactWriteReason.NOT_WRITABLE
    assert list(tmp_path.iterdir()) == []


def test_the_temporary_is_short_and_independent_of_the_artifact_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pins the fix rather than only its consequence: a prefix derived from the name again would
    # silently re-lower the ceiling asserted above, and this fails first, with the reason. The
    # destination is listed from inside os.fsync, the one moment the temporary is on disk.
    stem = "x" * 200
    seen: list[str] = []
    real_fsync = os.fsync

    def record(descriptor: int) -> None:
        seen.extend(entry.name for entry in tmp_path.iterdir())
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record)
    _sink(tmp_path).write(artifact_name(stem), b"payload", media=ArtifactMedia.SVG)
    assert len(seen) == 1
    assert seen[0].startswith(TEMPORARY_PREFIX)
    assert seen[0].endswith(TEMPORARY_SUFFIX)
    assert stem not in seen[0], "the temporary must not carry the artifact name"
    assert len(seen[0]) <= 32, "a fixed-width temporary, so its length is not the caller's to set"


@pytest.mark.parametrize("payload", PAYLOADS)
def test_bytes_land_verbatim(tmp_path: Path, payload: bytes) -> None:
    # The one byte-level promise this package makes. Legacy SVG bytes carry an XML prologue with
    # single quotes and no trailing newline; an encode, a newline translation or an appended byte
    # here would shift every differential hash and look like a renderer regression.
    receipt = _sink(tmp_path).write(artifact_name("page-000"), payload, media=ArtifactMedia.SVG)
    landed = tmp_path / "page-000.svg"
    assert sha256(landed.read_bytes()) == sha256(payload)
    assert landed.read_bytes() == payload
    assert receipt.byte_count == len(payload)


@given(payload=st.binary(max_size=4096))
def test_bytes_land_verbatim_for_arbitrary_payloads(payload: bytes) -> None:
    with tempfile.TemporaryDirectory() as raw:
        destination = Path(raw)
        _sink(destination).write(artifact_name("p"), payload, media=ArtifactMedia.PDF)
        assert (destination / "p.pdf").read_bytes() == payload


def test_a_large_payload_is_not_truncated(tmp_path: Path) -> None:
    payload = os.urandom(4_000_000)
    _sink(tmp_path).write(artifact_name("big"), payload, media=ArtifactMedia.PNG)
    assert sha256((tmp_path / "big.png").read_bytes()) == sha256(payload)


@pytest.mark.parametrize("media", list(ArtifactMedia))
def test_the_suffix_comes_from_the_media_and_nowhere_else(
    tmp_path: Path, media: ArtifactMedia
) -> None:
    _sink(tmp_path).write(artifact_name("artifact"), b"x", media=media)
    assert (tmp_path / f"artifact{SUFFIXES[media]}").is_file()


def test_the_suffix_table_is_exhaustive_over_the_enum() -> None:
    assert set(SUFFIXES) == set(ArtifactMedia)


def test_a_caller_supplied_suffix_is_kept_and_the_media_suffix_appended(tmp_path: Path) -> None:
    # Recorded rather than silently normalised: ArtifactName is a stem and ArtifactMedia is the
    # sole source of the suffix, so a CLI turning --output /tmp/out.pdf into a name must pass the
    # stem. Swallowing a matching suffix here would put a second opinion about the filename in
    # the only component that owns it.
    _sink(tmp_path).write(artifact_name("out.pdf"), b"x", media=ArtifactMedia.PDF)
    assert (tmp_path / "out.pdf.pdf").is_file()


def test_the_destination_is_created_including_missing_parents(tmp_path: Path) -> None:
    destination = tmp_path / "a" / "b" / "c"
    _sink(destination).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert (destination / "p.svg").is_file()


def test_an_existing_artifact_is_refused_then_permitted(tmp_path: Path) -> None:
    _sink(tmp_path).write(artifact_name("p"), b"first", media=ArtifactMedia.SVG)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(artifact_name("p"), b"second", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.ALREADY_PRESENT
    assert caught.value.name == "p"
    assert (tmp_path / "p.svg").read_bytes() == b"first"
    _sink(tmp_path, overwrite=True).write(artifact_name("p"), b"second", media=ArtifactMedia.SVG)
    assert (tmp_path / "p.svg").read_bytes() == b"second"


def test_a_dry_run_reports_truthfully_and_touches_nothing(tmp_path: Path) -> None:
    destination = tmp_path / "never-created"
    receipt = _sink(destination, dry_run=True).write(
        artifact_name("p"),
        b"twelve bytes",
        media=ArtifactMedia.PNG,
    )
    assert receipt.committed is False
    assert receipt.byte_count == 12
    assert receipt.uri.endswith("/never-created/p.png")
    assert not destination.exists()


def test_a_committing_write_says_so(tmp_path: Path) -> None:
    receipt = _sink(tmp_path).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert receipt.committed is True
    assert receipt.uri.startswith("file://")


def test_a_dry_run_does_not_refuse_an_existing_artifact(tmp_path: Path) -> None:
    _sink(tmp_path).write(artifact_name("p"), b"first", media=ArtifactMedia.SVG)
    receipt = _sink(tmp_path, dry_run=True).write(
        artifact_name("p"),
        b"second",
        media=ArtifactMedia.SVG,
    )
    assert receipt.committed is False
    assert (tmp_path / "p.svg").read_bytes() == b"first"


ERRNO_CASES: Sequence[tuple[int, ArtifactWriteReason]] = (
    (errno.EEXIST, ArtifactWriteReason.ALREADY_PRESENT),
    (errno.EACCES, ArtifactWriteReason.NOT_WRITABLE),
    (errno.EPERM, ArtifactWriteReason.NOT_WRITABLE),
    (errno.EROFS, ArtifactWriteReason.NOT_WRITABLE),
    (errno.ENOTDIR, ArtifactWriteReason.NOT_WRITABLE),
    (errno.ENOENT, ArtifactWriteReason.NOT_WRITABLE),
    (errno.EISDIR, ArtifactWriteReason.NOT_WRITABLE),
    (errno.ENAMETOOLONG, ArtifactWriteReason.NOT_WRITABLE),
    (errno.ENOSPC, ArtifactWriteReason.OUT_OF_SPACE),
    (errno.EDQUOT, ArtifactWriteReason.OUT_OF_SPACE),
    (errno.EFBIG, ArtifactWriteReason.OUT_OF_SPACE),
    (errno.EIO, ArtifactWriteReason.INTERRUPTED),
    (errno.EINTR, ArtifactWriteReason.INTERRUPTED),
)


@pytest.mark.parametrize(("code", "reason"), ERRNO_CASES)
def test_the_errno_table_is_reached_by_injection_not_by_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    reason: ArtifactWriteReason,
) -> None:
    # Injection rather than a read-only directory: a chmod-based negative test passes vacuously
    # as root and leaves the whole table uncovered while looking tested.
    def explode(self: Path, _target: Path) -> None:
        raise OSError(code, os.strerror(code), str(self))

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is reason


def test_an_errno_less_oserror_is_reported_as_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_self: Path, _target: Path) -> None:
        raise OSError

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.INTERRUPTED


def test_a_failed_commit_leaves_no_temporary_and_no_partial_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_self: Path, _target: Path) -> None:
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(ArtifactWriteFailed):
        _sink(tmp_path).write(artifact_name("p"), b"x" * 1024, media=ArtifactMedia.SVG)
    assert list(tmp_path.iterdir()) == []


def test_a_failure_mid_write_leaves_nothing_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync

    def explode(descriptor: int) -> None:
        real_fsync(descriptor)
        raise OSError(errno.EIO, "disk fell over")

    monkeypatch.setattr(os, "fsync", explode)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.INTERRUPTED
    assert list(tmp_path.iterdir()) == []


def test_an_undirectory_destination_is_not_writable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"i am a file")
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(blocker / "under").write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.NOT_WRITABLE


@pytest.mark.parametrize("value", ["../escape", "a/b", "a\\b", ".", "..", "trailing.", " pad "])
def test_a_traversing_name_cannot_even_be_constructed(value: str) -> None:
    # The check lives in the domain, once, before any adapter is chosen -- which is why the sink
    # does not repeat it.
    with pytest.raises(ValueError, match="artifact name"):
        ArtifactName(value=value)


def test_a_temporary_that_was_never_created_is_not_unlinked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other arm of the cleanup guard: when NamedTemporaryFile itself fails there is nothing to
    # remove, and an unconditional unlink would raise a second, misleading error over the first.
    def explode(**_kwargs: object) -> object:
        raise OSError(errno.ENOSPC, "no space for a temporary")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", explode)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path).write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.OUT_OF_SPACE
    assert list(tmp_path.iterdir()) == []


def test_a_destination_that_cannot_be_created_is_reported_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_self: Path, **_kwargs: object) -> None:
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(Path, "mkdir", explode)
    with pytest.raises(ArtifactWriteFailed) as caught:
        _sink(tmp_path / "nested").write(artifact_name("p"), b"x", media=ArtifactMedia.SVG)
    assert caught.value.reason is ArtifactWriteReason.NOT_WRITABLE
