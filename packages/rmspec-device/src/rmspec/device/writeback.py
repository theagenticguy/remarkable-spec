"""Guarded read-modify-write of one page's scene artifact, over SSH.

This is the concurrency-safety layer, and it exists because of the north star: a human may
be holding the stylus while an agent writes to the same tablet over the same cable.
``rmspec.device.ssh`` can already *create* a document; this module is the only place in the
workspace that **replaces** bytes a human made by hand, so every defect it can have is a
lost stroke.

The three properties, and the defect each one closes
----------------------------------------------------
`.claude/PRIOR-ART.md` measures ``remarkable-mcp``, the closest project to this north star
and an actively maintained one. Its ``draw`` is the reference implementation of all three
defects at once, which is what makes them worth naming rather than merely avoiding.

1. **Every write carries a precondition, and a mismatch is a refusal.**
   :meth:`SshSceneWriter.read_scene` captures the artifact's identity -- the lowercase hex
   SHA-256 of its bytes, the same value ``rmspec.formats.fingerprint`` computes and
   ``SyncedPage.rm_hash`` persists -- and :meth:`SshSceneWriter.write_scene` re-reads and
   re-compares it immediately before the rename. If it moved, the write is refused and
   nothing on the device changes. There is no merge path and no clobber path, because a
   refusal is recoverable by re-reading and re-applying while a lost stroke is not
   recoverable at all. ``remarkable-mcp`` compares no etag, no mtime and no hash between
   its read and its write, so whichever of the two sets of strokes lands second wins and
   the other is gone with no error anywhere.

   The bytes are the only identity available. ``RemoteShell`` has four methods and no
   ``stat``, so there is no mtime to compare, and a re-read is honest at these sizes -- the
   real page this design was measured against is 18,355 bytes.

2. **The write is a rename, not a truncation.** The new bytes go to a temp path *in the
   page's own directory*, and one ``mv -f`` puts them in place. ``mv`` within a filesystem
   is ``rename(2)``: it either replaces the artifact completely or leaves it untouched, and
   a link that drops mid-transfer therefore costs a stray temp file rather than a truncated
   page. ``remarkable-mcp`` writes with ``cat > path`` over SSH stdin, which truncates in
   place before the first byte arrives, stages its temp file host-side only, and has no
   ``mv`` anywhere -- so a dropped cable leaves a partial ``.rm``. This cable dropped twice
   during one session of building this project.

3. **One snapshot per write, in a ring, outside the xochitl tree.** See
   :data:`SNAPSHOT_ROOT` and :meth:`SshSceneWriter.undo`. ``remarkable-mcp`` writes
   ``{pageId}.rm.bak`` only when one does not already exist, so the second write to a page
   leaves the ``.bak`` holding the pre-*first*-write state: rollback reaches virgin and
   never one step back, and the change a user actually wants returned is the one that is
   gone. A ``.bak`` that holds only the first pre-write state is worse than none, because
   it looks like a safety net.

Why ``mv`` over the exec channel and not SFTP rename
----------------------------------------------------
The instinct is to prefer SFTP, and it is wrong here for two independent reasons.

* Plain ``SSH_FXP_RENAME`` -- what ``paramiko.SFTPClient.rename`` sends, and the only rename
  SFTP protocol 3 defines -- **fails when the destination exists**. Replacing a page is
  exactly that case. The variant with POSIX ``rename(2)`` semantics is
  ``posix_rename``/``posix-rename@openssh.com``, a server extension, and whether this
  firmware's SFTP subsystem advertises it is **unmeasured**. ``mv -f`` is ``rename(2)``
  unconditionally.
* :class:`~rmspec.device._shell.RemoteShell` exposes four methods and its docstring argues
  at length for why it is closed at four. Adding a fifth for rename would put a
  transport-specific verb into the seam every double implements, in exchange for a
  primitive the exec channel already has.

So the write primitive is ``write_file`` to a temp path plus ``run(mv -f)``, both of which
the existing Protocol provides. ``ParamikoShell.write_file`` already stats the path after
writing and raises ``DeviceTransferInterrupted`` on a short landing, which is necessary and
not sufficient: it proves the temp file is complete *before* anything replaces the page.

The window that stays open, stated plainly
------------------------------------------
**The precondition shrinks the race; it does not close it.** Between the re-read and the
``mv`` there are two round trips -- the snapshot write and the rename -- and a stroke the
human commits inside that interval is still overwritten. Closing it completely needs a lock
the firmware does not offer: there is no advisory lock, no compare-and-swap, and no
transaction over the xochitl store, and reMarkable's own documentation says only that
xochitl *should not be running* while these files are touched.

What the precondition buys is that the *large* window -- read, think, encode, transfer,
which is seconds to minutes -- becomes a *small* one measured in round trips, and that the
common case of "the human drew while the agent was thinking" is a typed refusal instead of
silent data loss. That is the honest claim. Anything stronger would be prose this project
has already had to correct twice.

Nothing here restarts xochitl
-----------------------------
A page written this way is **not** visible until the document is reopened, and this module
will not force it: stock Paper Pro firmware limits xochitl to four starts per ten minutes
and maps start-limit failure onto ``emergency.target``, whose handler reboots the tablet.
:class:`~rmspec.device.ssh.SshUploader` accepts that cost because it creates a document that
no listing would otherwise report -- and pays it behind four guard commands, which is the
honest measure of how expensive the cost is. An edit to an existing page has no such need, so
there is no ``systemctl`` in this module and no code path that wants one. A caller that needs the
human to see the edit now should say so to the human.

Not exported from ``rmspec.device``
-----------------------------------
``rmspec.device.__all__`` means "bindable to a port in ``rmspec.domain.ports.device``", and
there is no page-writer Protocol there yet. So these names live in a public *module* --
reachable as ``rmspec.device.writeback``, exactly like ``rmspec.device.addresses`` -- and
this class joins that list on the day the port exists. Adding the port from here was not an
option: this package may import ``rmspec.domain`` and may not change it.
"""

from __future__ import annotations

import contextlib
import hashlib
from typing import TYPE_CHECKING, Annotated, Final

from pydantic import BaseModel, Field

from rmspec.device._shell import PathUnreadableError
from rmspec.device.addresses import SCENE_SUFFIX, RemoteCommand, RemotePath, document_paths
from rmspec.domain.errors import (
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    TransportKind,
)

if TYPE_CHECKING:
    from rmspec.device._shell import RemoteShell

__all__ = [
    "ABSENT_IDENTITY",
    "MAKE_DIR_TEMPLATE",
    "MOVE_TEMPLATE",
    "PART_STAMP_LENGTH",
    "PART_SUFFIX",
    "REMOVE_TEMPLATE",
    "SNAPSHOT_DEPTH",
    "SNAPSHOT_DIR_EXPECTED",
    "SNAPSHOT_DIR_GOT",
    "SNAPSHOT_GONE",
    "SNAPSHOT_NAME_WIDTH",
    "SNAPSHOT_ROOT",
    "SNAPSHOT_WANTED",
    "UNDO_CREATE_OPERATION",
    "ScenePrecondition",
    "SceneRead",
    "SceneWriteReceipt",
    "SshSceneWriter",
]

# ─────────────────────────── the commands, all BusyBox-safe ───────────────────────────
#
#  Three templates, each a literal spelled in this module, each argument quoted by
#  `RemoteCommand.of`. BusyBox 1.36.1 provides all three applets and none of the flags used
#  here is a GNU long option.

#: Create the snapshot directory for one page, and its parents. Idempotent, which is why it
#: runs on every write rather than being remembered: a memo of "the directory exists" is a
#: process-lifetime cache of device state, and this module has none of those.
MAKE_DIR_TEMPLATE: Final = "mkdir -p {}"

#: Replace the page with the temp file. The whole of requirement 2 is this one command:
#: within a filesystem ``mv`` is ``rename(2)``, so the page is either the old bytes or the
#: new ones and never a prefix of either. ``-f`` because the destination normally exists and
#: an interactive prompt on a non-tty would hang rather than ask.
MOVE_TEMPLATE: Final = "mv -f {} {}"

#: Remove one file this module created: a temp file whose write or rename failed, or a
#: snapshot aged out of the ring. Never used on anything the user or xochitl made -- both
#: arguments it is ever given are names composed in this module under
#: :data:`SNAPSHOT_ROOT` or with :data:`PART_SUFFIX`.
REMOVE_TEMPLATE: Final = "rm -f {}"


# ─────────────────────────── where things go ───────────────────────────

#: Where snapshots live: a cache directory this package owns, **outside** the xochitl tree.
#:
#: Requirement 3 says the snapshots must not litter the store, and this is the mechanism
#: rather than the intention. ``remarkable-mcp`` puts its ``.bak`` beside the page inside the
#: document directory, where it accumulates permanently with no cleanup and where anything
#: walking the store -- including this project's own ``SshBundleSource`` -- has to know to
#: ignore it. Under ``.cache`` the whole history is one subtree a user can delete with one
#: command, no listing of the store is affected, and the name says the bytes are disposable.
#:
#: It is under ``/home/root`` like the store itself, so a snapshot and the page it came from
#: are on the same filesystem -- which is not needed to *write* a snapshot but is needed to
#: rename one back, and :meth:`SshSceneWriter.undo` goes through the same atomic path as any
#: other write.
SNAPSHOT_ROOT: Final = RemotePath.absolute("/home/root/.cache/rmspec/scene-snapshots")

#: How many snapshots one page keeps. Three, and the trade-off is deliberate:
#:
#: * **One** would satisfy "undo the most recent write" and nothing more, and a burst of
#:   agent writes -- the exact thing this project is for -- would destroy the pre-burst state
#:   on the second write.
#: * **Unbounded** is ``remarkable-mcp``'s other failure repeated in a new place: a page
#:   edited a thousand times would hold a thousand copies of itself.
#:
#: Three bounds the footprint at three page-sized files per *page ever written*, which for
#: the measured 18,355-byte page is 55 KiB against 45 GiB free. What is restorable through
#: this API is the newest, via :meth:`SshSceneWriter.undo`; the other two exist so a burst
#: does not erase its own starting point and so a human with an SSH session can recover a
#: deeper step by hand. :meth:`SshSceneWriter.snapshots` lists them so that bound is
#: something a caller can check rather than something this docstring asserts.
SNAPSHOT_DEPTH: Final = 3

#: Zero-padding of a snapshot's sequence number, for a listing that sorts the way a reader
#: expects. Cosmetic only: sequences are compared as integers after parsing, never as text,
#: so the ten-thousandth write to one page widens the name and breaks nothing.
SNAPSHOT_NAME_WIDTH: Final = 4

#: Suffix of the temp file the new bytes land in before the rename. Deliberately **not**
#: ending in ``.rm``: the page directory is xochitl's, and a file it might mistake for a
#: scene artifact is not a risk worth taking for a nicer filename.
PART_SUFFIX: Final = ".rmspec-part"

#: How much of the payload's digest goes into the temp filename. Enough that two agents
#: writing *different* bytes to one page cannot collide on the temp path, while two writing
#: *identical* bytes collide on a file whose contents they agree about. Deriving the name
#: from the payload rather than from a clock or a counter is what makes it unique without
#: this class taking an injected id generator it would otherwise have no use for.
PART_STAMP_LENGTH: Final = 16


# ─────────────────────────── what a refusal says ───────────────────────────

#: How :meth:`SshSceneWriter.write_scene` spells "there was no artifact here" in the
#: ``expected``/``got`` of a refusal. A sentence rather than a digest, because
#: ``sha256(b"")`` is a real digest of a real zero-byte page and the two states are
#: different: 86 of the 194 real scene artifacts measured on the reference device are
#: zero-length, and every one of them exists.
ABSENT_IDENTITY: Final = "no artifact at all"

#: What :meth:`SshSceneWriter.undo` says it wanted when the snapshot a receipt names has
#: gone -- aged out of the ring by three later writes, or deleted with the cache.
SNAPSHOT_WANTED: Final = "the snapshot this receipt names"

#: What it got instead. ``PathUnreadableError`` cannot tell absent from refused, so neither
#: can this.
SNAPSHOT_GONE: Final = "no readable file"

#: What a snapshot directory that ``mkdir -p`` has just reported success for is expected to
#: be. If listing it then fails per-path the store has contradicted itself, and that is a
#: protocol error rather than "this page has no snapshots".
SNAPSHOT_DIR_EXPECTED: Final = "a directory that mkdir -p has just created"

#: What it was instead.
SNAPSHOT_DIR_GOT: Final = "a directory that could not be listed"

#: The one thing :meth:`SshSceneWriter.undo` refuses, with ``supported_by=()`` -- **no**
#: transport can do it, so a caller has nothing to retry with.
#:
#: Undoing a write that *created* the artifact means deleting a file, and this package does
#: not delete from the user's store. ``SshUploader`` states the same rule for the same
#: reason: removal is itself destructive, and a wrong guess about what a path held is not
#: recoverable. An empty ``supported_by`` is a claim and not a blank, which is the convention
#: ``UnsupportedField`` and ``NO_SERIAL_SOURCE`` already use.
UNDO_CREATE_OPERATION: Final = "undo a scene write that created the artifact"


#: A lowercase hex SHA-256. Constrained on the way in so a caller that upper-cases a digest,
#: or passes a truncated one, fails at construction instead of getting a refusal on every
#: write that would look exactly like a concurrent human.
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _digest(raw: bytes, /) -> str:
    """Fingerprint one artifact's bytes.

    Parameters
    ----------
    raw
        The artifact's complete contents.

    Returns
    -------
    str
        Lowercase hex SHA-256 -- the same value ``rmspec.formats.fingerprint.rm_hash``
        computes and ``SyncedPage.rm_hash`` persists, so a precondition captured here is
        comparable with one the sync database already recorded. Spelled again rather than
        imported because ``rmspec.device`` may import ``rmspec.domain`` and nothing else in
        the workspace; the two are held together by both being ``sha256(raw).hexdigest()``
        of the whole file and by this sentence.
    """
    return hashlib.sha256(raw).hexdigest()


def _identity(digest: str | None, /) -> str:
    """Describe one artifact's identity for the text of a refusal.

    Parameters
    ----------
    digest
        The fingerprint, or ``None`` for an artifact that was not there.

    Returns
    -------
    str
        The digest prefixed with its algorithm, or :data:`ABSENT_IDENTITY`.
    """
    return ABSENT_IDENTITY if digest is None else f"sha256 {digest}"


class ScenePrecondition(BaseModel, frozen=True, extra="forbid"):
    """One page's identity at the moment it was read, and the condition of writing to it.

    Built by :attr:`SceneRead.precondition` rather than by hand in the normal case, which is
    what makes the safe form the easy one: a caller cannot obtain the bytes to modify
    without also obtaining the value that guards putting them back.

    Constructing one directly is legitimate and :meth:`SshSceneWriter.undo` does it -- the
    digest it wants is the one *its own* write produced, not one it read.
    """

    doc_uuid: str = Field(min_length=1)
    """The document holding the page, exactly as the store spells it."""

    page_id: str = Field(min_length=1)
    """The page, exactly as the ``.content`` page order spells it."""

    digest: Sha256Hex | None = None
    """The artifact's fingerprint when it was read, or ``None`` for a page that had no
    artifact at all.

    ``None`` and ``sha256(b"")`` are different preconditions and both are reachable: the
    firmware's own representation of a page with no ink is a **zero-length** ``.rm``, and 86
    of the 194 real ones measured are exactly that. So "the file was not there" and "the file
    was there and empty" are two device states, and a write that expected the first must be
    refused if it finds the second -- somebody created the page in between."""


class SceneRead(BaseModel, frozen=True, extra="forbid"):
    """One page's bytes and where they came from, with the precondition derived, not stored.

    :attr:`precondition` is a property over :attr:`scene` rather than a field, so there is no
    way to hold a value whose digest disagrees with its bytes. That is the same argument
    ``rmspec.device.addresses`` makes for :class:`~rmspec.device.addresses.RemoteCommand`:
    the unsafe combination is made unrepresentable instead of forbidden in prose.
    """

    doc_uuid: str = Field(min_length=1)
    """The document that was read."""

    page_id: str = Field(min_length=1)
    """The page that was read."""

    path: RemotePath
    """Where it was read from, for a caller that reports or logs the source."""

    scene: bytes | None
    """The artifact's complete contents, or ``None`` when there was no artifact.

    Not collapsed the way :class:`~rmspec.device.ssh.SshBundleSource` collapses them -- that
    class maps both an absent artifact and a zero-length one to ``None``, because for
    *rendering* both mean "this page carries no ink" and the distinction has no consumer.
    Here it has one: it is half the precondition. :class:`~rmspec.device.ssh.SshSearchIndexSource`
    draws the same line in the same direction for the same reason."""

    @property
    def precondition(self) -> ScenePrecondition:
        """Derive the condition under which these bytes may be written back.

        Returns
        -------
        ScenePrecondition
            Carrying this read's identifiers and the digest of :attr:`scene`, or a ``None``
            digest when there was no artifact.
        """
        return ScenePrecondition(
            doc_uuid=self.doc_uuid,
            page_id=self.page_id,
            digest=None if self.scene is None else _digest(self.scene),
        )


class SceneWriteReceipt(BaseModel, frozen=True, extra="forbid"):
    """What one guarded write did, including how to undo it.

    Reports what the transport observed and no more. In particular it does **not** report
    that the human can see the edit: a page replaced this way is invisible until the document
    is reopened, and this module does not restart xochitl to change that. See the module
    docstring for why not.
    """

    doc_uuid: str = Field(min_length=1)
    """The document written to."""

    page_id: str = Field(min_length=1)
    """The page written to."""

    path: RemotePath
    """The artifact the rename put in place."""

    byte_count: int = Field(ge=0)
    """How many bytes the page now holds. Equal to the payload's length: the temp file's size
    was confirmed by ``write_file`` before the rename, and the rename moves all of it or
    none."""

    digest: Sha256Hex
    """Fingerprint of the bytes written. This is the precondition of undoing this write --
    :meth:`SshSceneWriter.undo` passes it back, so an undo is refused if the human has drawn
    since."""

    snapshot: RemotePath | None = None
    """Where the replaced bytes were kept, or ``None`` when there was no artifact to replace.

    ``None`` is what makes :meth:`SshSceneWriter.undo` raise instead of guessing: undoing a
    creation means deleting, and this package does not delete from the store."""

    pruned: tuple[RemotePath, ...] = ()
    """Snapshots removed to keep the ring at :data:`SNAPSHOT_DEPTH`, oldest first. Empty
    until the page has been written to more than that many times."""


class SshSceneWriter:
    """Replace one page's scene artifact, atomically, under a precondition, with a snapshot.

    The whole of the concurrency-safety design lives in the two methods a caller sequences:
    :meth:`read_scene` then :meth:`write_scene`. :meth:`undo` and :meth:`snapshots` exist so
    the snapshot is a usable safety net rather than a file that looks like one.

    Bound to no port. There is no page-writer Protocol in ``rmspec.domain.ports.device``
    yet, and this package may not add one -- see the module docstring.

    What this class does not do
    ---------------------------
    * It does not encode or decode a scene. The bytes it is handed come from a
      ``PageCodec``, which lives in ``rmspec.formats`` and which this package may not import.
      That separation is why the payload arrives as opaque ``bytes``: the lossless-re-encode
      precondition described in ``DESIGN-writeback.md`` is the codec's to check, and a
      transport re-checking it would be a second opinion that could disagree.
    * It does not create a page directory. ``mkdir -p`` here is for the snapshot ring only.
      A document whose directory does not exist yet is a document being *created*, which is
      :class:`~rmspec.device.ssh.SshUploader`'s operation; a write into a missing directory
      surfaces as ``DeviceProtocolError`` naming the path, which is the honest report.
    * It does not restart xochitl, and nothing it does needs a restart.
    * It caches nothing. Every precondition is a fresh read of the file itself, never a
      lookup in a listing something else already took -- which matters because
      :meth:`~rmspec.device.ssh.SshCatalog.list_documents` *is* memoised, per instance, and a
      precondition drawn from that memo would be exactly as stale as the memo.

    Parameters
    ----------
    shell
        The transport. Nothing else in this class touches a wire.
    root
        The xochitl root the document lives under. A parameter rather than the constant, as
        in every other adapter, so a test can build a synthetic tree anywhere.
    snapshot_root
        Where the snapshot rings live, defaulting to :data:`SNAPSHOT_ROOT`.
    depth
        How many snapshots one page keeps, defaulting to :data:`SNAPSHOT_DEPTH`.

    Raises
    ------
    ValueError
        *depth* is below one. A ring with no slots is a writer with no snapshot, which is
        the state requirement 3 exists to forbid, so it is refused at construction rather
        than degraded to. A composition-root fact reported as one.
    """

    def __init__(
        self,
        *,
        shell: RemoteShell,
        root: RemotePath,
        snapshot_root: RemotePath = SNAPSHOT_ROOT,
        depth: int = SNAPSHOT_DEPTH,
    ) -> None:
        if depth < 1:
            msg = f"a snapshot ring needs at least one slot, and depth was {depth}"
            raise ValueError(msg)
        self._shell = shell
        self._root = root
        self._snapshot_root = snapshot_root
        self._depth = depth

    def read_scene(self, doc_uuid: str, page_id: str, /) -> SceneRead:
        """Read one page and capture its identity in the same call.

        The identity is captured here rather than by the caller because the caller cannot be
        relied on to do it -- that is the entire defect this module closes. A
        :class:`SceneRead` cannot be built without its bytes and derives its digest from
        them, so "read the page and forget to record what it was" is not expressible.

        Parameters
        ----------
        doc_uuid
            The document holding the page.
        page_id
            The page, as the ``.content`` page order spells it.

        Returns
        -------
        SceneRead
            The bytes and the path, with :attr:`SceneRead.precondition` ready to hand to
            :meth:`write_scene`. ``scene`` is ``None`` when there is no artifact -- either
            absent or unreadable, which the transport cannot tell apart -- and ``b""`` when
            there is a zero-length one, which is the firmware's normal way of spelling a
            page with no ink.

        Raises
        ------
        ValueError
            *doc_uuid* or *page_id* is not usable as one path component.
        DeviceUnreachable
            The tablet did not answer, or the read stalled.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The channel misbehaved.
        """
        path = self._page(doc_uuid, page_id)
        try:
            scene: bytes | None = self._shell.read_file(path)
        except PathUnreadableError:
            scene = None
        return SceneRead(doc_uuid=doc_uuid, page_id=page_id, path=path, scene=scene)

    def write_scene(self, precondition: ScenePrecondition, scene: bytes, /) -> SceneWriteReceipt:
        """Replace one page's artifact, refusing if it moved since it was read.

        Seven steps, ordered so that everything that can fail without touching the page fails
        first and the interval between the check and the commit is as short as this transport
        allows:

        1. ``mkdir -p`` the page's snapshot directory.
        2. list it, to allocate the next sequence number.
        3. prune it to :data:`SNAPSHOT_DEPTH` minus one, making room for this write's
           snapshot. Before the page is touched, so a prune that fails costs nothing but
           undo depth.
        4. write *scene* to a temp path in the page's own directory. The slow step, and
           deliberately outside the window: ``write_file`` confirms the landed size, so what
           the rename later moves is known to be complete.
        5. **re-read the page and compare its digest to** ``precondition.digest``. A mismatch
           raises and nothing has changed. **The window opens here.**
        6. write the bytes just verified to the snapshot ring. They are provably the bytes
           this write is about to replace, which is what the ``.bak`` in the prior art is
           not.
        7. ``mv -f`` the temp file over the page. **The window closes here.**

        The window is therefore steps 5 to 7: one write and one rename. A stroke committed by
        the human inside it is still lost, and no arrangement of these calls fixes that --
        only a lock would, and the firmware offers none. What is fixed is the seconds-to-
        minutes window that read-encode-write leaves open by default, and it is fixed by
        refusing rather than merging: a refusal is recoverable by re-reading and re-applying,
        and a lost stroke is not recoverable at all.

        If any of steps 4 to 7 fails, the temp file is removed on a best-effort basis and the
        original failure propagates -- a cleanup failure must not replace the error that
        prompted it. Removal is safe here in a way it is not for the store generally: the
        name was composed in this module from the payload's own digest, so nothing else can
        have put it there.

        Parameters
        ----------
        precondition
            What the page was when it was read, from :attr:`SceneRead.precondition`.
        scene
            The complete new contents. ``b""`` is accepted: a zero-length artifact is how
            the firmware itself spells a page with no ink, so refusing it would refuse a
            legitimate state. Keeping the *edit* additive is the codec's guarantee, not this
            transport's.

        Returns
        -------
        SceneWriteReceipt
            Naming the artifact, the digest written, the snapshot to undo with, and anything
            the ring pruned.

        Raises
        ------
        DeviceProtocolError
            The page is no longer what it was when it was read, and the write was **refused**
            -- ``route`` is the page, ``expected`` is the identity captured at read time and
            ``got`` is the identity found now, either as ``sha256 <hex>`` or as
            :data:`ABSENT_IDENTITY`. Also raised when the snapshot directory cannot be listed
            after ``mkdir -p`` reported success, and when a command exits non-zero.

            **This is the closest error in ``rmspec.domain.errors`` and it is not an exact
            fit**, which is recorded here rather than worked around. Its own docstring says
            it means "the device answered but broke its own contract", and a human picking up
            a stylus breaks no contract of the firmware's -- the contract broken is the
            *caller's* precondition. What the domain wants is a sibling of
            ``DeviceTransferInterrupted``: a stale-precondition refusal carrying the subject,
            the expected digest and the observed one, and a remediation of "re-read the page
            and re-apply the edit", which is the one piece of advice this class cannot attach
            because ``DeviceProtocolError`` takes none. Until that error exists, the three
            fields carry the whole story and a caller can match on ``route`` being the page
            path.
        DeviceTransferInterrupted
            The temp file or the snapshot landed short. The page is untouched: a short temp
            file is detected before anything renames it.
        DeviceUnreachable
            The tablet did not answer, or a step stalled.
        DeviceAuthFailed
            The device refused the credentials.
        ValueError
            An identifier in *precondition* is not usable as one path component.
        """
        path = self._page(precondition.doc_uuid, precondition.page_id)
        digest = _digest(scene)
        part = path.with_suffix(f".{digest[:PART_STAMP_LENGTH]}{PART_SUFFIX}")
        ring = self._ring(precondition.doc_uuid, precondition.page_id)
        self._shell.run(RemoteCommand.of(MAKE_DIR_TEMPLATE, ring))
        held = self._sequences(ring)
        pruned = self._prune(ring, held, keep=self._depth - 1)
        try:
            self._shell.write_file(part, scene)
            replaced = self.verify(precondition)
            snapshot = self._keep(ring, held, replaced)
            self._shell.run(RemoteCommand.of(MOVE_TEMPLATE, part, path))
        except DeviceError:
            self._discard(part)
            raise
        return SceneWriteReceipt(
            doc_uuid=precondition.doc_uuid,
            page_id=precondition.page_id,
            path=path,
            byte_count=len(scene),
            digest=digest,
            snapshot=snapshot,
            pruned=pruned,
        )

    def verify(self, precondition: ScenePrecondition, /) -> bytes | None:
        """Re-read the page and refuse if its identity has moved. Reads only.

        :meth:`write_scene` calls this itself, immediately before the rename, so calling it
        first buys **no** additional safety -- the check that matters is the one inside the
        write, and a check performed earlier would only widen the interval it guards. What it
        buys is cheapness: an agent that is about to spend real time encoding strokes, or a
        round trip transferring them, can learn here that its plan is already stale and stop.

        It is also the only way to exercise the precondition against a real device without
        writing to it, which is what ``test_device_hardware.py`` uses it for.

        Parameters
        ----------
        precondition
            What the page was when it was read.

        Returns
        -------
        bytes | None
            The page's current bytes, which are provably the ones *precondition* describes, or
            ``None`` when there is no artifact and the precondition agreed there would not be.

        Raises
        ------
        DeviceProtocolError
            The identity moved. Both identities are named -- see :meth:`write_scene` for the
            shape and for the domain gap this error stands in for. An unreadable artifact reads
            as absent here, exactly as it did in :meth:`read_scene`: the transport cannot tell
            absent from refused, so the pair is at least consistent, and the residual case is a
            page that is present but permission-refused whose precondition therefore says
            ``None``. On a device where this package authenticates as root that case does not
            arise; it is named because it is the one hole in the check.
        DeviceUnreachable
            The tablet did not answer, or the read stalled.
        DeviceAuthFailed
            The device refused the credentials.
        ValueError
            An identifier in *precondition* is not usable as one path component.
        """
        path = self._page(precondition.doc_uuid, precondition.page_id)
        try:
            current: bytes | None = self._shell.read_file(path)
        except PathUnreadableError:
            current = None
        observed = None if current is None else _digest(current)
        if observed != precondition.digest:
            raise DeviceProtocolError(
                transport=TransportKind.SSH,
                route=path.value,
                expected=_identity(precondition.digest),
                got=_identity(observed),
            )
        return current

    def undo(self, receipt: SceneWriteReceipt, /) -> SceneWriteReceipt:
        """Put back what one write replaced, under the same guarantees as the write.

        Goes through :meth:`write_scene`, which is the point: an undo is a read-modify-write
        like any other, so it is atomic, it is precondition-checked, and it takes its own
        snapshot -- which makes it redoable. The precondition it uses is
        :attr:`SceneWriteReceipt.digest`, the identity that write left behind, so an undo is
        **refused** if the human has drawn on the page since. Rolling back over somebody
        else's newer work would be the same data loss this module exists to prevent, arriving
        through the recovery path.

        Parameters
        ----------
        receipt
            The receipt of the write to undo.

        Returns
        -------
        SceneWriteReceipt
            The receipt of the restoring write, whose own snapshot holds what was just
            undone.

        Raises
        ------
        DeviceOperationUnsupported
            :attr:`SceneWriteReceipt.snapshot` is ``None``: the write created the artifact, so
            undoing it means deleting a file from the user's store, and no transport in this
            package does that. ``supported_by`` is empty, which is a claim -- there is nothing
            to retry with -- rather than a blank.
        DeviceProtocolError
            The snapshot the receipt names is gone, or the page is no longer what this write
            left. See :meth:`write_scene` for the shape of the refusal and for the domain gap
            it papers over.
        DeviceTransferInterrupted
            The restoring write landed short. The page is untouched.
        DeviceUnreachable
            The tablet did not answer, or a step stalled.
        DeviceAuthFailed
            The device refused the credentials.
        """
        if receipt.snapshot is None:
            raise DeviceOperationUnsupported(
                transport=TransportKind.SSH,
                operation=UNDO_CREATE_OPERATION,
                supported_by=(),
            )
        try:
            held = self._shell.read_file(receipt.snapshot)
        except PathUnreadableError as gone:
            raise DeviceProtocolError(
                transport=TransportKind.SSH,
                route=receipt.snapshot.value,
                expected=SNAPSHOT_WANTED,
                got=SNAPSHOT_GONE,
            ) from gone
        return self.write_scene(
            ScenePrecondition(
                doc_uuid=receipt.doc_uuid,
                page_id=receipt.page_id,
                digest=receipt.digest,
            ),
            held,
        )

    def snapshots(self, doc_uuid: str, page_id: str, /) -> tuple[RemotePath, ...]:
        """List one page's snapshot ring, newest first.

        Exists so :data:`SNAPSHOT_DEPTH` is a bound a caller can verify rather than one this
        module asserts, and so a human recovering a deeper step knows what to look at. Only
        the newest is restorable through :meth:`undo`; the rest are for a person with an SSH
        session.

        Parameters
        ----------
        doc_uuid
            The document.
        page_id
            The page.

        Returns
        -------
        tuple[RemotePath, ...]
            At most :data:`SNAPSHOT_DEPTH` paths, highest sequence first. Empty when the page
            has never been written to by this class, or when the cache directory has been
            deleted -- both of which are the same absence and neither of which is an error.

        Raises
        ------
        ValueError
            *doc_uuid* or *page_id* is not usable as one path component.
        DeviceUnreachable
            The tablet did not answer, or the listing stalled.
        DeviceAuthFailed
            The device refused the credentials.
        DeviceProtocolError
            The channel misbehaved.
        """
        ring = self._ring(doc_uuid, page_id)
        try:
            names = self._shell.list_dir(ring)
        except PathUnreadableError:
            return ()
        return tuple(self._named(ring, seq) for seq in sorted(_parsed(names), reverse=True))

    def _page(self, doc_uuid: str, page_id: str, /) -> RemotePath:
        """Locate one page's scene artifact.

        Parameters
        ----------
        doc_uuid
            The document.
        page_id
            The page.

        Returns
        -------
        RemotePath
            ``<root>/<doc_uuid>/<page_id>.rm``, with both identifiers validated as single
            path components on the way -- by the same function
            :class:`~rmspec.device.ssh.SshBundleSource` uses, so the two cannot disagree
            about where a page is.
        """
        return document_paths(self._root, doc_uuid).page(page_id)

    def _ring(self, doc_uuid: str, page_id: str, /) -> RemotePath:
        """Locate one page's snapshot directory.

        Parameters
        ----------
        doc_uuid
            The document.
        page_id
            The page.

        Returns
        -------
        RemotePath
            ``<snapshot_root>/<doc_uuid>/<page_id>``. One directory per page rather than one
            flat directory of composite names, so listing a ring is exactly one page's
            history and the sequence numbers need no prefix to disambiguate.
        """
        return self._snapshot_root.child(doc_uuid).child(page_id)

    def _sequences(self, ring: RemotePath, /) -> tuple[int, ...]:
        """List a snapshot directory ``mkdir -p`` has just created.

        Parameters
        ----------
        ring
            The page's snapshot directory.

        Returns
        -------
        tuple[int, ...]
            Every sequence number the ring holds, unsorted.

        Raises
        ------
        DeviceProtocolError
            The directory could not be listed although ``mkdir -p`` reported success. Not
            degraded to "no snapshots": that would mean allocating sequence zero over a ring
            that already holds one, and overwriting a snapshot is the failure mode this
            module is built to avoid.
        """
        try:
            return _parsed(self._shell.list_dir(ring))
        except PathUnreadableError as unlistable:
            raise DeviceProtocolError(
                transport=TransportKind.SSH,
                route=ring.value,
                expected=SNAPSHOT_DIR_EXPECTED,
                got=SNAPSHOT_DIR_GOT,
            ) from unlistable

    def _prune(
        self,
        ring: RemotePath,
        held: tuple[int, ...],
        /,
        *,
        keep: int,
    ) -> tuple[RemotePath, ...]:
        """Remove the oldest snapshots until at most *keep* remain.

        Runs before anything touches the page, so a failure here propagates with the page
        untouched and costs only undo depth.

        Parameters
        ----------
        ring
            The page's snapshot directory.
        held
            The sequence numbers it holds.
        keep
            How many to leave. ``depth - 1``, making room for this write's own snapshot.

        Returns
        -------
        tuple[RemotePath, ...]
            What was removed, oldest first. Empty when the ring was not yet full.

        Raises
        ------
        DeviceProtocolError
            A ``rm -f`` exited non-zero.
        DeviceUnreachable
            The tablet did not answer.
        DeviceAuthFailed
            The device refused the credentials.
        """
        doomed = sorted(held)[: max(len(held) - keep, 0)]
        removed = [self._named(ring, seq) for seq in doomed]
        for path in removed:
            self._shell.run(RemoteCommand.of(REMOVE_TEMPLATE, path))
        return tuple(removed)

    def _keep(
        self,
        ring: RemotePath,
        held: tuple[int, ...],
        replaced: bytes | None,
        /,
    ) -> RemotePath | None:
        """Store the bytes this write is about to replace.

        Parameters
        ----------
        ring
            The page's snapshot directory.
        held
            The sequence numbers it held before pruning, so the next one is monotonic even
            when pruning has just emptied the ring.
        replaced
            The verified pre-write bytes, or ``None`` when there was no artifact.

        Returns
        -------
        RemotePath | None
            Where they were kept, or ``None`` when there was nothing to keep. ``None`` is
            what makes :meth:`undo` refuse rather than delete.

        Raises
        ------
        DeviceTransferInterrupted
            The snapshot landed short. Raised before the page is renamed, so a write whose
            snapshot failed does not happen.
        DeviceUnreachable
            The tablet did not answer.
        DeviceAuthFailed
            The device refused the credentials.
        """
        if replaced is None:
            return None
        path = self._named(ring, max(held) + 1 if held else 0)
        self._shell.write_file(path, replaced)
        return path

    def _discard(self, part: RemotePath, /) -> None:
        """Remove a temp file whose write or rename did not complete, best effort.

        A failure to clean up is suppressed. It cannot be acted on, and letting it propagate
        would replace the error that prompted the cleanup -- which for the refusal path would
        turn "the human drew on this page" into "rm failed", losing the only diagnosis worth
        having. Same rule, and the same reasoning, as
        :meth:`~rmspec.device._shell.ParamikoShell.close`.

        Parameters
        ----------
        part
            The temp path, composed in this module from the payload's digest.
        """
        with contextlib.suppress(DeviceError):
            self._shell.run(RemoteCommand.of(REMOVE_TEMPLATE, part))

    @staticmethod
    def _named(ring: RemotePath, sequence: int, /) -> RemotePath:
        """Locate one snapshot by its sequence number.

        Parameters
        ----------
        ring
            The page's snapshot directory.
        sequence
            The snapshot's number.

        Returns
        -------
        RemotePath
            ``<ring>/NNNN.rm``, zero-padded to :data:`SNAPSHOT_NAME_WIDTH`.
        """
        return ring.child(f"{sequence:0{SNAPSHOT_NAME_WIDTH}d}{SCENE_SUFFIX}")


def _parsed(names: tuple[str, ...], /) -> tuple[int, ...]:
    """Read the sequence numbers out of a snapshot directory listing.

    Anything that is not ``<decimal digits>.rm`` is ignored rather than raised on. The
    directory belongs to this module, so nothing should be there -- but it sits under a cache
    root a user may reasonably poke at, and a stray file is not a reason to refuse to write a
    page. ``str.isdecimal`` rather than ``str.isdigit``: the latter accepts superscripts,
    which ``int`` then rejects.

    Parameters
    ----------
    names
        Bare entry names, as ``list_dir`` returns them.

    Returns
    -------
    tuple[int, ...]
        The sequence numbers, in listing order.
    """
    stems = [name.removesuffix(SCENE_SUFFIX) for name in names if name.endswith(SCENE_SUFFIX)]
    return tuple(int(stem) for stem in stems if stem.isdecimal())
