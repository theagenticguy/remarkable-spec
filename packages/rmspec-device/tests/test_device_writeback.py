"""The concurrency-safety layer: atomic replace, a precondition, a snapshot ring, no cache.

Four properties are asserted here, and each one is the inverse of a measured defect in
``remarkable-mcp`` -- see ``.claude/PRIOR-ART.md`` and the module docstring of
``rmspec.device.writeback``.

1. A page is replaced by ``mv``, never truncated in place, and a failure at any step leaves
   the page exactly as it was.
2. A write whose page moved since it was read is **refused**, never merged and never
   clobbered.
3. Every write leaves a snapshot of the bytes it replaced, in a bounded ring outside the
   xochitl tree, and the newest one is restorable.
4. Nothing memoises device state for longer than one command.

How a concurrent human is simulated
-----------------------------------
There is no second writer in this suite, and there does not need to be: the precondition is
a comparison between a digest captured at read time and the bytes found at write time, so
"the human drew in between" is *exactly* "the store holds different bytes when the check
runs". :class:`_MovingShell` is an in-memory store, so a test mutates ``shell.files`` between
the read and the write and the adapter cannot tell that apart from a stylus. Doing it with a
real second writer would need two SSH sessions against the author's tablet and would prove
the same equality.

Why the double interprets three commands
----------------------------------------
:class:`~rmspec.device.testing.FakeRemoteShell` deliberately does not emulate a shell -- its
``run`` answers from a script and changes nothing -- which is right for the adapters that
only *read* through it. It cannot express the property under test here, because "the page
now holds the new bytes and the temp file is gone" is a statement about what ``mv`` did.
:class:`_MovingShell` therefore carries out exactly the three applets this module sends
(``mkdir -p``, ``mv -f``, ``rm -f``) and nothing else, recovering their arguments with
``shlex.split`` -- the inverse of the ``shlex.quote`` ``RemoteCommand.of`` applied. It lives
here rather than in the shipped doubles because it is emulation, and the shipped double's
whole argument is that it does none.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import ValidationError

import rmspec.device
from rmspec.device import writeback
from rmspec.device.addresses import (
    CONTENT_SUFFIX,
    METADATA_SUFFIX,
    SCENE_SUFFIX,
    SEARCH_INDEX_NAME,
    RemoteCommand,
    RemotePath,
    document_paths,
)
from rmspec.device.ssh import SshCatalog, SshSearchIndexSource
from rmspec.device.testing import FakeRemoteShell
from rmspec.device.writeback import (
    ABSENT_IDENTITY,
    MAKE_DIR_TEMPLATE,
    MOVE_TEMPLATE,
    PART_STAMP_LENGTH,
    PART_SUFFIX,
    REMOVE_TEMPLATE,
    SNAPSHOT_DEPTH,
    SNAPSHOT_DIR_EXPECTED,
    SNAPSHOT_DIR_GOT,
    SNAPSHOT_GONE,
    SNAPSHOT_ROOT,
    SNAPSHOT_WANTED,
    UNDO_CREATE_OPERATION,
    ScenePrecondition,
    SceneRead,
    SshSceneWriter,
)
from rmspec.domain.errors import (
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    TransportKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT: Final = RemotePath.root()
DOC: Final = "11111111-2222-3333-4444-555555555555"
OTHER_DOC: Final = "99999999-8888-7777-6666-555555555555"
PAGE: Final = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PAGE_PATH: Final = document_paths(ROOT, DOC).page(PAGE)
PAGE_DIR: Final = document_paths(ROOT, DOC).page_dir
RING: Final = SNAPSHOT_ROOT.child(DOC).child(PAGE)

#: The bytes standing in for a page the human has already drawn on, and the bytes an agent
#: wants to put there. Opaque on purpose: this module never parses a scene.
OLD: Final = b"the human's strokes"
NEW: Final = b"the human's strokes, plus a reply"

#: What the human drew while the agent was thinking.
CONCURRENT: Final = b"the human's strokes, plus a second arrow"

OLD_DIGEST: Final = hashlib.sha256(OLD).hexdigest()
NEW_DIGEST: Final = hashlib.sha256(NEW).hexdigest()
EMPTY_DIGEST: Final = hashlib.sha256(b"").hexdigest()

PART_PATH: Final = PAGE_PATH.with_suffix(f".{NEW_DIGEST[:PART_STAMP_LENGTH]}{PART_SUFFIX}")
MOVE_COMMAND: Final = RemoteCommand.of(MOVE_TEMPLATE, PART_PATH, PAGE_PATH).text
DISCARD_COMMAND: Final = RemoteCommand.of(REMOVE_TEMPLATE, PART_PATH).text
MAKE_RING_COMMAND: Final = RemoteCommand.of(MAKE_DIR_TEMPLATE, RING).text

#: The three applets :class:`_MovingShell` carries out, as ``shlex.split`` returns them.
_INTERPRETED: Final = (["mkdir", "-p"], ["mv", "-f"], ["rm", "-f"])


class _MovingShell(FakeRemoteShell):
    """A :class:`FakeRemoteShell` that actually performs ``mkdir -p``, ``mv -f`` and ``rm -f``.

    Everything else -- ``read_file``, ``write_file``, the two failure vocabularies, the
    ordered log -- is inherited unchanged, so an adapter cannot tell this apart from the
    shipped double except by the effect of the three commands.
    """

    def run(self, command: RemoteCommand, /) -> str:
        """Run one command, then apply it to the in-memory store if it is one of the three.

        Parameters
        ----------
        command
            The command, already quoted.

        Returns
        -------
        str
            Whatever the base double answers: ``""`` for an interpreted command that was not
            scripted, since success for these three is silence.

        Raises
        ------
        DeviceError
            Whatever the base double raises -- ``refuse_commands`` and ``fail_with`` are
            honoured first, so a test can still fail any of the three.
        """
        words = shlex.split(command.text)
        if words[:2] in _INTERPRETED:
            self.outputs.setdefault(command.text, "")
        answer = super().run(command)
        self._apply(words)
        return answer

    def list_dir(self, path: RemotePath, /) -> tuple[str, ...]:
        """List a directory by looking at what the store actually holds under it.

        Parameters
        ----------
        path
            The directory to list.

        Returns
        -------
        tuple[str, ...]
            Every immediate child name, sorted. The base double answers from a fixed map,
            which cannot show a snapshot that a write has just added.

        Raises
        ------
        PathUnreadableError
            The directory is not in the store, exactly as the base double reports it.
        """
        super().list_dir(path)
        prefix = f"{path.value}/"
        return tuple(
            sorted(
                name
                for name in (
                    key.removeprefix(prefix) for key in self.files if key.startswith(prefix)
                )
                if "/" not in name
            )
        )

    def _apply(self, words: list[str], /) -> None:
        """Carry out one interpreted command against the store.

        Parameters
        ----------
        words
            The command, unquoted. Anything that is not one of the three is ignored.
        """
        if words[:2] == ["mkdir", "-p"]:
            self.dirs.setdefault(words[2], ())
        elif words[:2] == ["mv", "-f"]:
            self.files[words[3]] = self.files.pop(words[2])
        elif words[:2] == ["rm", "-f"]:
            self.files.pop(words[2], None)


def _store(
    *,
    page: bytes | None = OLD,
    snapshots: Mapping[str, bytes] | None = None,
    ring_exists: bool = True,
    short_writes: Sequence[str] = (),
    refuse_commands: Sequence[str] = (),
    refuse_reads: Sequence[str] = (),
) -> _MovingShell:
    """Build a store holding one document with one page.

    Parameters
    ----------
    page
        The page's artifact, or ``None`` for a page that has none.
    snapshots
        Snapshot names already in the ring, mapped to their bytes.
    ring_exists
        Whether the ring directory is already there. ``mkdir -p`` creates it either way; the
        flag exists so a test can start from a page that has never been written.
    short_writes
        Paths whose write lands short.
    refuse_commands
        Command texts that exit non-zero.
    refuse_reads
        Paths whose read is refused although a listing named them.

    Returns
    -------
    _MovingShell
        The double.
    """
    files: dict[str, bytes] = {}
    if page is not None:
        files[PAGE_PATH.value] = page
    for name, data in (snapshots or {}).items():
        files[RING.child(name).value] = data
    dirs: dict[str, tuple[str, ...]] = {PAGE_DIR.value: ()}
    if ring_exists or snapshots:
        dirs[RING.value] = ()
    return _MovingShell(
        files=files,
        dirs=dirs,
        short_writes=short_writes,
        refuse_commands=refuse_commands,
        refuse_reads=refuse_reads,
    )


def _writer(shell: FakeRemoteShell, *, depth: int = SNAPSHOT_DEPTH) -> SshSceneWriter:
    """Build the writer under test over *shell*.

    Parameters
    ----------
    shell
        The transport double.
    depth
        The snapshot ring's depth.

    Returns
    -------
    SshSceneWriter
        The adapter.
    """
    return SshSceneWriter(shell=shell, root=ROOT, depth=depth)


# ─────────────────────────── requirement 2: atomic writes ───────────────────────────


def test_the_new_bytes_go_to_a_temp_path_first_and_arrive_by_rename() -> None:
    shell = _store()
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    written = [path for path, _ in shell.writes]
    assert PART_PATH.value in written
    assert PAGE_PATH.value not in written, "the page itself must never be opened for writing"
    assert MOVE_COMMAND in shell.commands
    assert shell.files[PAGE_PATH.value] == NEW
    assert PART_PATH.value not in shell.files, "the rename consumed the temp file"
    assert receipt.byte_count == len(NEW)
    assert receipt.digest == NEW_DIGEST
    assert receipt.path == PAGE_PATH


def test_the_temp_path_is_in_the_pages_own_directory_and_is_not_a_scene_artifact() -> None:
    """Same filesystem, so ``mv`` is ``rename(2)``; not ``.rm``, so xochitl ignores it."""
    assert PART_PATH.value.startswith(f"{PAGE_DIR.value}/")
    assert PART_PATH.value.endswith(PART_SUFFIX)
    assert not PART_PATH.value.endswith(SCENE_SUFFIX)


def test_the_temp_name_is_derived_from_the_payload_so_two_agents_do_not_collide() -> None:
    shell = _store()
    _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST), NEW
    )
    first = [path for path, _ in shell.writes if path.endswith(PART_SUFFIX)]

    other = _store()
    _writer(other).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        b"a different reply",
    )
    second = [path for path, _ in other.writes if path.endswith(PART_SUFFIX)]

    assert first != second


def test_a_short_temp_write_leaves_the_page_untouched() -> None:
    shell = _store(short_writes=[PART_PATH.value])

    with pytest.raises(DeviceTransferInterrupted):
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert shell.files[PAGE_PATH.value] == OLD
    assert MOVE_COMMAND not in shell.commands


def test_a_failed_rename_leaves_the_page_untouched_and_removes_the_temp_file() -> None:
    shell = _store(refuse_commands=[MOVE_COMMAND])

    with pytest.raises(DeviceProtocolError):
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert shell.files[PAGE_PATH.value] == OLD
    assert PART_PATH.value not in shell.files
    assert DISCARD_COMMAND in shell.commands


def test_a_cleanup_that_also_fails_does_not_replace_the_error_that_prompted_it() -> None:
    shell = _store(refuse_commands=[MOVE_COMMAND, DISCARD_COMMAND])

    with pytest.raises(DeviceProtocolError) as caught:
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert caught.value.route == MOVE_COMMAND
    assert shell.files[PAGE_PATH.value] == OLD


def test_a_transport_failure_before_the_ring_is_prepared_propagates() -> None:
    shell = _store()
    shell.fail_with = DeviceUnreachable(
        transport=TransportKind.SSH,
        endpoint="10.11.99.1:22",
        detail="the cable was pulled",
    )

    with pytest.raises(DeviceUnreachable):
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )


def test_nothing_this_module_sends_restarts_a_service() -> None:
    shell = _store()
    _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST), NEW
    )

    assert all("systemctl" not in command for command in shell.commands)
    assert all("xochitl" not in shlex.split(command)[0] for command in shell.commands)


# ─────────────────────── requirement 1: the precondition ───────────────────────


def test_reading_a_page_captures_its_identity_in_the_same_call() -> None:
    read = _writer(_store()).read_scene(DOC, PAGE)

    assert read.scene == OLD
    assert read.path == PAGE_PATH
    assert read.precondition == ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST)


def test_a_page_with_no_artifact_reads_as_an_absent_precondition() -> None:
    read = _writer(_store(page=None)).read_scene(DOC, PAGE)

    assert read.scene is None
    assert read.precondition.digest is None


def test_a_zero_length_artifact_is_not_the_same_state_as_an_absent_one() -> None:
    """86 of 194 real scene artifacts are zero-length, and every one of them exists."""
    empty = _writer(_store(page=b"")).read_scene(DOC, PAGE)
    absent = _writer(_store(page=None)).read_scene(DOC, PAGE)

    assert empty.scene == b""
    assert empty.precondition.digest == EMPTY_DIGEST
    assert absent.precondition.digest is None
    assert empty.precondition != absent.precondition


def test_a_page_the_human_changed_between_the_read_and_the_write_is_refused() -> None:
    shell = _store()
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)

    # The human draws. Nothing else in this test is different from the happy path.
    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError) as caught:
        writer.write_scene(read.precondition, NEW)

    assert caught.value.route == PAGE_PATH.value
    assert caught.value.expected == f"sha256 {OLD_DIGEST}"
    assert caught.value.got == f"sha256 {hashlib.sha256(CONCURRENT).hexdigest()}"
    assert shell.files[PAGE_PATH.value] == CONCURRENT, "the human's strokes survive intact"
    assert MOVE_COMMAND not in shell.commands


def test_a_refusal_removes_the_temp_file_it_had_already_staged() -> None:
    shell = _store()
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)
    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError):
        writer.write_scene(read.precondition, NEW)

    assert PART_PATH.value not in shell.files


def test_a_page_that_vanished_between_the_read_and_the_write_is_refused() -> None:
    shell = _store()
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)
    del shell.files[PAGE_PATH.value]

    with pytest.raises(DeviceProtocolError) as caught:
        writer.write_scene(read.precondition, NEW)

    assert caught.value.got == ABSENT_IDENTITY
    assert caught.value.expected == f"sha256 {OLD_DIGEST}"


def test_a_page_created_between_the_read_and_the_write_is_refused() -> None:
    shell = _store(page=None)
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)
    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError) as caught:
        writer.write_scene(read.precondition, NEW)

    assert caught.value.expected == ABSENT_IDENTITY
    assert caught.value.got == f"sha256 {hashlib.sha256(CONCURRENT).hexdigest()}"
    assert shell.files[PAGE_PATH.value] == CONCURRENT


def test_an_unreadable_artifact_is_read_as_absent_by_both_halves_of_the_check() -> None:
    """The transport cannot tell absent from refused, so the pair is at least consistent."""
    shell = _store(refuse_reads=[PAGE_PATH.value])
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)

    assert read.precondition.digest is None
    receipt = writer.write_scene(read.precondition, NEW)
    assert receipt.snapshot is None


def test_writing_to_a_page_that_has_no_artifact_is_allowed_and_records_no_snapshot() -> None:
    shell = _store(page=None)
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=None),
        NEW,
    )

    assert shell.files[PAGE_PATH.value] == NEW
    assert receipt.snapshot is None


def test_an_empty_payload_is_a_legitimate_page_state_and_is_not_refused() -> None:
    shell = _store()
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        b"",
    )

    assert shell.files[PAGE_PATH.value] == b""
    assert receipt.byte_count == 0
    assert receipt.digest == EMPTY_DIGEST


def test_a_digest_that_is_not_lowercase_hex_sha256_is_refused_at_construction() -> None:
    for bad in (OLD_DIGEST.upper(), OLD_DIGEST[:63], f"{OLD_DIGEST}0", "not a digest"):
        with pytest.raises(ValidationError):
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=bad)


def test_a_scene_read_cannot_hold_a_digest_that_disagrees_with_its_bytes() -> None:
    """``precondition`` is derived, so the inconsistent pair is not representable."""
    read = SceneRead(doc_uuid=DOC, page_id=PAGE, path=PAGE_PATH, scene=OLD)

    assert read.precondition.digest == OLD_DIGEST
    # Through `model_validate` because the type checker rejects the keyword outright, which is
    # the same guarantee one layer earlier; `extra="forbid"` is what holds at runtime.
    with pytest.raises(ValidationError):
        SceneRead.model_validate(
            {
                "doc_uuid": DOC,
                "page_id": PAGE,
                "path": PAGE_PATH,
                "scene": OLD,
                "digest": OLD_DIGEST,
            }
        )


def test_an_identifier_that_is_not_one_path_component_is_a_wiring_bug_not_a_device_failure() -> (
    None
):
    writer = _writer(_store())

    with pytest.raises(ValueError, match="separator"):
        writer.read_scene("has/a/slash", PAGE)


def test_the_precondition_can_be_probed_without_writing_anything() -> None:
    """``verify`` is the read-only half, and it must send no write and no command."""
    shell = _store()
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)

    assert writer.verify(read.precondition) == OLD
    assert shell.writes == []
    assert shell.commands == []

    shell.files[PAGE_PATH.value] = CONCURRENT
    with pytest.raises(DeviceProtocolError):
        writer.verify(read.precondition)
    assert shell.writes == []
    assert shell.commands == []


def test_probing_first_is_optional_because_the_write_checks_again_itself() -> None:
    """A stale plan that passed an earlier probe is still refused by the write."""
    shell = _store()
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)
    assert writer.verify(read.precondition) == OLD

    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError):
        writer.write_scene(read.precondition, NEW)
    assert shell.files[PAGE_PATH.value] == CONCURRENT


def test_the_precondition_is_checked_immediately_before_the_rename() -> None:
    """The ordered log is the only way to assert on a window, so it is asserted on."""
    shell = _store()
    writer = _writer(shell)
    writer.write_scene(ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST), NEW)

    log = shell.log
    part = log.index(f"write {PART_PATH.value}")
    check = log.index(f"read {PAGE_PATH.value}")
    snapshot = log.index(f"write {RING.child('0000.rm').value}")
    move = log.index(f"run {MOVE_COMMAND}")

    assert part < check < snapshot < move, "the slow write is outside the window"
    assert log[check:move] == log[check : check + 2], "only the snapshot sits inside it"


# ─────────────────────── requirement 3: a snapshot per write ───────────────────────


def test_every_write_snapshots_the_bytes_it_replaced() -> None:
    shell = _store()
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.snapshot == RING.child("0000.rm")
    assert receipt.snapshot is not None
    assert shell.files[receipt.snapshot.value] == OLD


def test_the_second_write_snapshots_the_first_ones_result_not_the_original() -> None:
    """The defect in the prior art, stated as its inverse: the newest state is recoverable."""
    shell = _store()
    writer = _writer(shell)
    first = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST), NEW
    )
    later = b"and a third arrow"
    second = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=first.digest),
        later,
    )

    assert second.snapshot != first.snapshot
    assert first.snapshot is not None
    assert second.snapshot is not None
    assert shell.files[second.snapshot.value] == NEW, "one step back, not back to virgin"
    assert shell.files[first.snapshot.value] == OLD


def test_snapshots_live_outside_the_xochitl_tree() -> None:
    shell = _store()
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.snapshot is not None
    assert not receipt.snapshot.value.startswith(ROOT.value)
    assert receipt.snapshot.value.startswith(SNAPSHOT_ROOT.value)
    inside_store = [key for key in shell.files if key.startswith(f"{PAGE_DIR.value}/")]
    assert inside_store == [PAGE_PATH.value], "the page, and nothing this module left behind"


def test_the_ring_is_bounded_and_reports_what_it_pruned() -> None:
    shell = _store(snapshots={f"{index:04d}.rm": b"older" for index in range(SNAPSHOT_DEPTH)})
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.pruned == (RING.child("0000.rm"),)
    assert RING.child("0000.rm").value not in shell.files
    held = _writer(shell).snapshots(DOC, PAGE)
    assert len(held) == SNAPSHOT_DEPTH
    assert held[0] == RING.child(f"{SNAPSHOT_DEPTH:04d}.rm")


def test_a_ring_that_is_not_yet_full_prunes_nothing() -> None:
    shell = _store()
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.pruned == ()


def test_a_ring_of_depth_one_keeps_exactly_the_newest_snapshot() -> None:
    shell = _store(snapshots={"0005.rm": b"older"})
    receipt = _writer(shell, depth=1).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.pruned == (RING.child("0005.rm"),)
    assert receipt.snapshot == RING.child("0006.rm")
    assert _writer(shell).snapshots(DOC, PAGE) == (RING.child("0006.rm"),)


def test_a_ring_with_no_slots_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one slot"):
        SshSceneWriter(shell=_store(), root=ROOT, depth=0)


def test_the_sequence_number_never_reuses_a_name_the_ring_has_seen() -> None:
    shell = _store(snapshots={"0009.rm": b"older"})
    receipt = _writer(shell).write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    assert receipt.snapshot == RING.child("0010.rm")


def test_a_stray_file_in_the_ring_is_ignored_rather_than_fatal() -> None:
    shell = _store(snapshots={"0001.rm": b"older", "notes.txt": b"a human poked here"})
    writer = _writer(shell)

    assert writer.snapshots(DOC, PAGE) == (RING.child("0001.rm"),)
    receipt = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )
    assert receipt.snapshot == RING.child("0002.rm")
    assert shell.files[RING.child("notes.txt").value] == b"a human poked here"


def test_a_ring_entry_whose_stem_is_not_a_number_is_ignored() -> None:
    shell = _store(snapshots={"latest.rm": b"older", ".rm": b"odder"})

    assert _writer(shell).snapshots(DOC, PAGE) == ()


def test_a_page_that_has_never_been_written_reports_no_snapshots() -> None:
    assert _writer(_store(ring_exists=False)).snapshots(DOC, PAGE) == ()


def test_a_ring_that_cannot_be_listed_after_mkdir_succeeded_is_a_contradiction() -> None:
    """Not degraded to "no snapshots": that would allocate a name the ring already holds."""
    shell = FakeRemoteShell(
        files={PAGE_PATH.value: OLD},
        dirs={PAGE_DIR.value: ()},
        outputs={MAKE_RING_COMMAND: ""},
    )

    with pytest.raises(DeviceProtocolError) as caught:
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert caught.value.route == RING.value
    assert caught.value.expected == SNAPSHOT_DIR_EXPECTED
    assert caught.value.got == SNAPSHOT_DIR_GOT
    assert shell.files[PAGE_PATH.value] == OLD


def test_a_short_snapshot_write_stops_the_write_before_the_page_changes() -> None:
    shell = _store(short_writes=[RING.child("0000.rm").value])

    with pytest.raises(DeviceTransferInterrupted):
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert shell.files[PAGE_PATH.value] == OLD
    assert PART_PATH.value not in shell.files


def test_a_failed_prune_stops_the_write_before_the_page_changes() -> None:
    doomed = RemoteCommand.of(REMOVE_TEMPLATE, RING.child("0000.rm")).text
    shell = _store(
        snapshots={f"{index:04d}.rm": b"older" for index in range(SNAPSHOT_DEPTH)},
        refuse_commands=[doomed],
    )

    with pytest.raises(DeviceProtocolError):
        _writer(shell).write_scene(
            ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
            NEW,
        )

    assert shell.files[PAGE_PATH.value] == OLD


def test_the_newest_snapshot_is_restorable_and_the_undo_is_itself_snapshotted() -> None:
    shell = _store()
    writer = _writer(shell)
    receipt = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )

    undone = writer.undo(receipt)

    assert shell.files[PAGE_PATH.value] == OLD
    assert undone.digest == OLD_DIGEST
    assert undone.snapshot is not None
    assert shell.files[undone.snapshot.value] == NEW, "the undo is redoable"


def test_an_undo_is_refused_when_the_human_has_drawn_since_the_write() -> None:
    shell = _store()
    writer = _writer(shell)
    receipt = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )
    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError) as caught:
        writer.undo(receipt)

    assert caught.value.expected == f"sha256 {NEW_DIGEST}"
    assert shell.files[PAGE_PATH.value] == CONCURRENT


def test_undoing_a_write_that_created_the_page_would_mean_deleting_and_is_refused() -> None:
    shell = _store(page=None)
    writer = _writer(shell)
    receipt = writer.write_scene(ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=None), NEW)

    with pytest.raises(DeviceOperationUnsupported) as caught:
        writer.undo(receipt)

    assert caught.value.operation == UNDO_CREATE_OPERATION
    assert caught.value.supported_by == ()
    assert shell.files[PAGE_PATH.value] == NEW, "nothing was deleted"


def test_an_undo_whose_snapshot_has_gone_says_so_rather_than_writing_something_else() -> None:
    shell = _store()
    writer = _writer(shell)
    receipt = writer.write_scene(
        ScenePrecondition(doc_uuid=DOC, page_id=PAGE, digest=OLD_DIGEST),
        NEW,
    )
    assert receipt.snapshot is not None
    del shell.files[receipt.snapshot.value]

    with pytest.raises(DeviceProtocolError) as caught:
        writer.undo(receipt)

    assert caught.value.route == receipt.snapshot.value
    assert caught.value.expected == SNAPSHOT_WANTED
    assert caught.value.got == SNAPSHOT_GONE


# ─────────────────── requirement 4: no process-lifetime memoisation ───────────────────


def test_the_writer_re_reads_the_page_on_every_precondition_capture() -> None:
    shell = _store()
    writer = _writer(shell)

    first = writer.read_scene(DOC, PAGE)
    shell.files[PAGE_PATH.value] = CONCURRENT
    second = writer.read_scene(DOC, PAGE)

    assert first.scene == OLD
    assert second.scene == CONCURRENT
    assert shell.reads.count(PAGE_PATH.value) == 2


def test_the_writer_holds_no_state_between_calls() -> None:
    """An adapter with no instance attributes beyond its collaborators cannot go stale."""
    writer = _writer(_store())

    assert set(vars(writer)) == {"_shell", "_root", "_snapshot_root", "_depth"}


def test_the_search_index_source_re_reads_rather_than_memoising_the_image() -> None:
    index = ROOT.child(SEARCH_INDEX_NAME)
    shell = FakeRemoteShell(files={index.value: b"first"})
    source = SshSearchIndexSource(shell, root=ROOT)

    assert source.read_index() == b"first"
    shell.files[index.value] = b"second"
    assert source.read_index() == b"second"


def test_the_catalog_listing_is_memoised_for_one_instance_and_not_for_the_process() -> None:
    """``Scope.REQUEST`` is what makes the catalog's memo safe, so both halves are pinned.

    A second call on the *same* catalog must not re-list -- that is the memo the port
    documents, and one command is one instance. A *new* catalog over the same transport must
    see the store as it is now -- that is what makes the memo's lifetime a request rather than
    a process, and it is the property ``remarkable-mcp``'s TTL-less client-instance cache does
    not have.
    """
    shell = FakeRemoteShell(dirs={ROOT.value: (f"{DOC}{METADATA_SUFFIX}",)})
    catalog = SshCatalog(shell=shell, root=ROOT)

    assert len(catalog.list_documents().skipped) == 1
    shell.dirs[ROOT.value] = (f"{DOC}{METADATA_SUFFIX}", f"{OTHER_DOC}{METADATA_SUFFIX}")

    assert len(catalog.list_documents().skipped) == 1, "one instance is one view"
    assert shell.listings.count(ROOT.value) == 1
    assert len(SshCatalog(shell=shell, root=ROOT).list_documents().skipped) == 2


def test_a_memoised_listing_cannot_defeat_the_precondition() -> None:
    """The two caches that exist and the check that must not consult them, together.

    A catalog whose listing was taken before the human drew is exactly the stale state
    ``Scope.REQUEST`` tolerates -- and the write must still be refused, because the
    precondition is a fresh read of the artifact and never a lookup in anything a listing
    already produced.
    """
    metadata = json.dumps(
        {
            "type": "DocumentType",
            "visibleName": "a notebook",
            "lastModified": "1700000000000",
            "parent": "",
        }
    ).encode()
    shell = _store()
    shell.files[ROOT.child(f"{DOC}{METADATA_SUFFIX}").value] = metadata
    shell.files[ROOT.child(f"{DOC}{CONTENT_SUFFIX}").value] = json.dumps(
        {"formatVersion": 2, "fileType": "notebook", "cPages": {"pages": [{"id": PAGE}]}}
    ).encode()
    shell.dirs[ROOT.value] = (
        f"{DOC}{METADATA_SUFFIX}",
        f"{DOC}{CONTENT_SUFFIX}",
        DOC,
    )
    catalog = SshCatalog(shell=shell, root=ROOT)
    writer = _writer(shell)
    read = writer.read_scene(DOC, PAGE)
    catalog.list_documents()

    shell.files[PAGE_PATH.value] = CONCURRENT

    with pytest.raises(DeviceProtocolError):
        writer.write_scene(read.precondition, NEW)
    assert shell.files[PAGE_PATH.value] == CONCURRENT


# ─────────────────────────── the module's own surface ───────────────────────────


def test_the_modules_public_names_are_declared_and_sorted() -> None:
    assert writeback.__all__ == sorted(
        writeback.__all__,
        key=lambda name: (name.isupper() is False, name),
    )
    surfaced = {
        name
        for name in vars(writeback)
        if not name.startswith("_") and name != "annotations" and name.isidentifier()
    }
    assert set(writeback.__all__) <= surfaced


def test_the_writer_is_not_bindable_from_the_packages_public_surface() -> None:
    """It is not a port binding, because the port does not exist yet. See the module docstring."""
    assert "SshSceneWriter" not in rmspec.device.__all__
    assert not hasattr(rmspec.device, "SshSceneWriter")
