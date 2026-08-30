"""Write an answer onto a page a human is holding, in ink, while the tablet is powered on.

This is the north star: a person and an agent working the same page. It is also the only write
path in this package that touches something a human made by hand, so every decision below is
about what may not be lost or misrepresented.

**The reply is ink because typed text is invisible.** A page-scoped typed-text block written
into a real page by a foreign author was *preserved* by firmware 3.27.3.0 across the tablet's
own re-save -- read back at the exact position set, with the foreign author id intact -- and
was **never drawn**. It was confirmed the hard way: a second "reply here" arrow was drawn on
the page after the first reply had already landed on it. Strokes are what xochitl renders, so a
reply a human can read has to be ink, and this module composes strokes rather than text. Anyone
replacing :class:`~rmspec.domain.ports.render.TextEngraver` with the simpler thing that writes a
text block will produce a page that reads back perfectly and shows nothing.

What this use case is, mechanically
-----------------------------------
Three ports and one ordering. :class:`~rmspec.domain.ports.render.TextEngraver` turns the
message into strokes; :class:`~rmspec.domain.ports.formats.SceneAppender` folds those strokes
into the page's bytes, append-only, so the human's existing ink is a literal prefix of the
result and cannot be damaged even if everything else goes wrong;
:class:`~rmspec.domain.ports.device.SceneWriter` replaces the page atomically, having captured
its identity at read time and re-checked it immediately before the replacement lands.

The ordering is the policy. **Everything refusable is refused before the device is touched at
all**, because engraving is local and free: a message that cannot be drawn, or that would not
fit on the page, costs zero round trips to discover. What remains after those refusals is one
read, one append, one write.

The non-ASCII decision, which is the interesting one
---------------------------------------------------
The engraving face draws 95 printable ASCII characters. Model-written prose is full of ``--``
as an em dash, curly quotes, an ellipsis and a typographic apostrophe, none of which are among
them; each becomes a **struck box** on the page and is reported on
:attr:`~rmspec.domain.ports.render.InkText.substituted`.

Three placements were available for the fold, and two of them are wrong:

*Folding here* -- mapping an em dash to ``--``, curly quotes to straight ones -- is convenient
and it is a silent edit to somebody's words, made by the layer with the least right to make
one. The render slice already refused to guess what ``--`` meant, and that refusal is inherited
rather than re-litigated. Worse, the fold would be **invisible in the result**: ``substituted``
reports characters the *font* could not draw, not characters this module rewrote, so a folded
reply would look like a reply that needed no folding. Silence about an edit to a human's page is
the one thing this whole design exists to remove.

*Refusing outright, with no way through*, would be this layer overruling a caller about an
action that is **reversible**: :meth:`~rmspec.domain.ports.device.SceneWriter.undo` exists, and
it is atomic, snapshotted and precondition-checked, so a reply drawn with three boxes in it
costs one command to take back. That is a genuinely different weighing from
:mod:`rmspec.app.create`, where the firmware's route table is closed at six families, none of
them deletes, and a wrong upload costs a manual delete on the tablet. Treating a reversible edit
as though it were that would be dishonest about the risk.

So: **refuse by default, allow explicitly, and report either way.**
:attr:`ReplyOnPageRequest.allow_substituted_characters` defaults to ``False`` and the refusal is
a :class:`~rmspec.domain.errors.UsageError` naming every character at fault, raised **before the
first device call** -- which is what makes the load-bearing property hold mechanically rather
than by care: nobody can spend a write and only then discover the tablet shows boxes. Setting
the flag proceeds, records one
:attr:`~rmspec.domain.errors.DegradationKind.INK_CHARACTER_SUBSTITUTED` per distinct character,
and reports them on :attr:`ReplyOnPageResult.substituted`.

The degradation is recorded even though the caller asked for it, and that is deliberate: the
opt-in authorises the *write*, not the silence. An agent that set the flag once, for one reply
that needed it, would otherwise draw boxes on every later page with nothing to notice, and
``--strict`` at the shell boundary is what turns that into a non-zero exit. It is the one place
this package differs from :mod:`rmspec.app.create`'s opted-in duplicate name, which records
nothing -- that reports a fact about the *library* while the bytes uploaded are exactly the
caller's, whereas here the artifact on the page differs from the text that was requested.

Why the fit check is here and not on the appender
-------------------------------------------------
The reference corpus has 13 of 30 pages carrying ink outside the declared x range and 17 outside
y, one reaching y 81,159 on a page declaring 2,160. So a coordinate-range validator on a scene
would refuse documents the tablet itself wrote, which is why neither
:class:`~rmspec.domain.ports.formats.SceneAppender` nor this module has one.

Ink *this program is about to place* is a different question with a different answer. A reply
below the bottom edge is invisible, which is the same defect as the typed text block, and it is
knowable for free from :attr:`~rmspec.domain.ports.render.InkText.extent_mm` before anything is
written. One check, over the extent the engraver actually reported rather than over the box that
was asked for, covers an origin off the page and a word too long to wrap in one condition.

What is deliberately not here
-----------------------------
**No history entry.** :class:`~rmspec.domain.models.SyncOperation` has five members and none of
them is an in-place page edit; filing this under ``PUSH`` would record "a file uploaded to the
tablet", which is not what happened, and the audit log's whole value is that its history reads
correctly later. The reviewed alternative is a new member, and adding one to record something no
command reads yet is speculation this module may not perform on the domain's behalf. The record
that does exist is the one that matters operationally: the device-side snapshot
:attr:`~rmspec.domain.ports.device.SceneWriteReceipt.snapshot` names, per write.

**No preview.** Rendering the proposed page for a human to look at needs a
:class:`~rmspec.domain.ports.render.PageRenderer`, a palette and a background -- the whole
render policy -- and a use case that both previews and writes has to decide what to do when
nobody is looking at the preview. Previewing is the caller's step, run against this module's
inputs before it is called.

**No creation.** A page the device stores no scene for cannot be appended to, and this module
does not invent one: :class:`~rmspec.domain.errors.SceneRewriteUnsafe` is raised for exactly the
reason the appender would raise it, one layer earlier, because the ``scene is None`` state the
device port models deliberately is a fact this module holds and passing ``b""`` on would erase
the distinction its own port drew.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from rmspec.app._degradations import DegradationLog
from rmspec.domain.errors import Degradation, DegradationKind, SceneRewriteUnsafe, UsageError
from rmspec.domain.models import ScreenSpec
from rmspec.domain.ports.device import SceneWriteReceipt
from rmspec.domain.ports.render import InkTextStyle, PhysicalSize

if TYPE_CHECKING:
    from rmspec.domain.ports.device import ScenePrecondition, SceneWriter
    from rmspec.domain.ports.formats import SceneAppender, SceneEdit
    from rmspec.domain.ports.render import InkText, TextEngraver

__all__ = ["ReplyOnPage", "ReplyOnPageRequest", "ReplyOnPageResult"]


class ReplyOnPageRequest(BaseModel, frozen=True, extra="forbid"):
    """One message to draw on one page, and where on the page to draw it."""

    doc_uuid: str = Field(min_length=1)
    """The document to write into, as the device identifies it.

    Constrained rather than checked in the method body, following the split
    :mod:`rmspec.app` states: the domain names no error for a blank identifier, so a pydantic
    constraint keeps the state unconstructible instead.
    """

    page_id: str = Field(min_length=1)
    """The page to write on, as the device identifies it."""

    text: str
    """The message, as the caller wants it read.

    Unconstrained by pydantic and stripped by :meth:`ReplyOnPage.reply`, whose
    :class:`~rmspec.domain.errors.UsageError` is what a whitespace-only reply becomes. The
    refusal is here rather than left to the engraver's own precondition because stripping
    happens here: the string the engraver would see is not the string the caller passed, and an
    error about an empty reply has to be about what was typed.
    """

    screen: ScreenSpec
    """The screen the page is measured against.

    Required and never defaulted, for the reason :class:`~rmspec.domain.ports.render.RenderStyle`
    gives about the same parameter: a wrong screen produces correctly-shaped ink in the wrong
    place, and here that means a reply the human cannot see.
    """

    style: InkTextStyle
    """Em size, line height, colour and thickness for the engraved ink."""

    left_mm: float = Field(ge=0)
    """Left edge of the text box, in millimetres from the page's left edge."""

    top_mm: float = Field(ge=0)
    """Top edge of the text box, in millimetres from the page's top edge."""

    width_mm: float = Field(gt=0)
    """Width of the text box, in millimetres. Lines wrap inside it and it does not grow."""

    allow_substituted_characters: bool = False
    """Whether to write a reply the engraving face cannot fully draw.

    Defaults to refusing. Setting it accepts a struck box on the page wherever a character was
    undrawable, records one
    :attr:`~rmspec.domain.errors.DegradationKind.INK_CHARACTER_SUBSTITUTED` per distinct
    character, and reports them on :attr:`ReplyOnPageResult.substituted`. The opt-in exists
    because the write is reversible and a struck box is a legible marker rather than a
    corruption; the default is the refusal because the alternative is discovering it on a page
    somebody is holding.
    """


class ReplyOnPageResult(BaseModel, frozen=True, extra="forbid"):
    """What landed on the page, what it says, and how to take it back.

    No field has a default: a caller cannot construct this without stating what was drawn, what
    was substituted for it, and what the human can currently see -- which is not the reply.
    """

    receipt: SceneWriteReceipt
    """The transport's record of the write, carried whole rather than unpacked.

    Whole because it is the undo token.
    :meth:`~rmspec.domain.ports.device.SceneWriter.undo` takes this value, so a caller holding
    this result can reverse the write without reassembling anything -- and reassembly is where
    two fields get transposed and the wrong snapshot is restored over the wrong page.

    It is also where this result says what the human can see.
    :attr:`~rmspec.domain.ports.device.SceneWriteReceipt.visibility` is
    :attr:`~rmspec.domain.ports.device.SceneVisibility.REOPEN_REQUIRED` and its enum has no
    member meaning "already visible", so this result cannot claim the reply is on screen. It is
    not: firmware 3.27.3.0 holds an open document's scene in memory and will not redraw the page
    until the document is reopened, and nothing here restarts the tablet's UI to force it --
    four starts in ten minutes reaches a target whose handler reboots the device. The document
    and the page identifiers live here too rather than being echoed alongside, so there is one
    source for them.
    """

    author_id: int = Field(gt=0)
    """The CRDT author component the reply's ids were minted under, from the appender.

    Greater than every author already in the artifact, which is what makes the minted ids
    collision-free by construction. Measured on firmware 3.27.3.0: a foreign author id written
    this way was accepted and *kept* through the tablet's own re-save, so this is the identity a
    later reader attributes the reply to.
    """

    layer_index: int = Field(ge=0)
    """Which layer the reply landed on, indexed as the page codec reports layers.

    A choice the appender made and the caller did not, reported so it can be rendered and
    disagreed with. It is a fact about these bytes only: the tablet renumbers scene ids across
    its own re-save, so a caller must re-derive it rather than store it.
    """

    lines: tuple[str, ...]
    """The reply as it was wrapped, one entry per drawn line, in order.

    What is actually on the page, which is not always what was passed in: wrapping is the
    engraver's decision, and a struck box occupies the place of the character it replaced.
    """

    stroke_count: int = Field(gt=0)
    """How many strokes the reply added. Positive: a reply that drew nothing is not a reply."""

    extent_mm: PhysicalSize
    """The real-world size of the box the ink occupies, as engraved.

    Reported rather than merely checked, because the next reply on the same page has to start
    below this one and a caller placing a second message needs the first one's height.
    """

    substituted: tuple[str, ...]
    """The distinct characters drawn as struck boxes, in first-appearance order.

    Empty on every reply that did not opt in, because a non-empty one is refused. Non-empty
    means the caller opted in and the page now shows a box wherever each of these appeared.
    """

    degradations: tuple[Degradation, ...]
    """Every substitution this reply made instead of failing.

    One ``INK_CHARACTER_SUBSTITUTED`` per entry in :attr:`substituted`, and nothing else. There
    is deliberately nothing here about the device: a write onto somebody's handwriting has
    nothing it may substitute, so every other outcome on this path is a refusal rather than a
    degraded success.
    """


class ReplyOnPage:
    """Draw one message on one page of a document on the attached tablet.

    Three collaborators, all Protocols: the engraver that turns words into ink, the appender
    that folds ink into a page's bytes without being able to damage what is already there, and
    the writer that replaces the page under a precondition and keeps a snapshot.

    Notes
    -----
    The order is the contract, and it is asserted rather than described::

        result = replier.reply(request)
        # every refusal above happened before the first device call
        writer.undo(result.receipt)  # and this is what makes it reversible
    """

    def __init__(
        self,
        *,
        engraver: TextEngraver,
        appender: SceneAppender,
        writer: SceneWriter,
    ) -> None:
        self._engraver = engraver
        self._appender = appender
        self._writer = writer

    def reply(self, request: ReplyOnPageRequest, /) -> ReplyOnPageResult:
        """Engrave the message, append it to the page, and replace the page atomically.

        Parameters
        ----------
        request
            The message, where to put it, and whether undrawable characters are acceptable.

        Returns
        -------
        ReplyOnPageResult
            What was drawn, the receipt that reverses it, and every substitution made.

        Raises
        ------
        UsageError
            The reply is whitespace only; the engraving face cannot draw one of its characters
            and :attr:`ReplyOnPageRequest.allow_substituted_characters` is not set; or the ink
            would not fit on the page. Every one of these is raised before the tablet is touched
            at all. Also raised by the appender for an empty stroke set, and by the writer for
            empty bytes -- neither reachable from here, since a non-blank reply engraves to at
            least one stroke and an appended scene is never empty.
        SceneRewriteUnsafe
            The page stores no scene, so there is nothing to append to and this is a scene to
            *create* rather than one to amend; or the appender will not rewrite the bytes it was
            given -- a round trip this build cannot reproduce, or a scene with no visible layer
            the ink could be seen on. Nothing has been written in either case.
        CorruptPageData
            The page's bytes are not a decodable scene file, so there is nothing to append to.
        DeviceStateMismatchError
            The page changed between the read and the write: the human drew, or another writer
            landed first. It carries both identities and ``retryable=True``, because the refusal
            happens before the replacement -- re-read, re-compose, and decide again. Never
            merged, and never resolved by writing last.
        DeviceDocumentNotFound
            The tablet holds no such document, or the identifier names a folder.
        MalformedDeviceMetadata
            The document exists and its page order could not be decoded.
        DeviceTransferInterrupted
            A transfer ended early. The page is as it was: an incomplete copy is never renamed
            into place.
        DeviceUnreachable
            The tablet did not answer. Never degraded into a successful reply.
        DeviceAuthFailed
            The tablet refused the credentials.
        DeviceProtocolError
            The tablet answered with something the transport cannot interpret.
        """
        ink = self._engraved(request)
        precondition, edit = self._appended(request, ink)
        receipt = self._writer.write_scene(precondition, edit.scene)
        log = DegradationLog()
        for character in ink.substituted:
            log.record(
                Degradation(
                    kind=DegradationKind.INK_CHARACTER_SUBSTITUTED,
                    subject=request.page_id,
                    detail=(
                        f"{character!r} (U+{ord(character):04X}) is not in the engraving face, "
                        f"so the page shows a struck box wherever it appeared"
                    ),
                    substituted="a struck box",
                )
            )
        return ReplyOnPageResult(
            receipt=receipt,
            author_id=edit.author_id,
            layer_index=edit.layer_index,
            lines=ink.lines,
            stroke_count=len(ink.strokes),
            extent_mm=ink.extent_mm,
            substituted=ink.substituted,
            degradations=log.frozen(),
        )

    def _engraved(self, request: ReplyOnPageRequest, /) -> InkText:
        """Turn the message into ink, refusing anything the tablet could not show honestly.

        Every check here costs no round trip, which is the whole reason they are all here: a
        caller cannot spend a write and then discover that its prose was drawn as boxes or that
        half the reply is below the bottom edge.

        Parameters
        ----------
        request
            The request as the caller built it.

        Returns
        -------
        InkText
            The strokes, the wrapped lines, the substituted characters and the extent.

        Raises
        ------
        UsageError
            The reply is whitespace only, holds characters the face cannot draw without the
            caller having opted in, or occupies a box that runs off the page.
        """
        text = request.text.strip()
        if not text:
            raise UsageError(
                subject=f"a reply of {len(request.text)} whitespace character(s)",
                requirement="a message with something in it to draw",
            )
        ink = self._engraver.engrave(
            text,
            screen=request.screen,
            style=request.style,
            left_mm=request.left_mm,
            top_mm=request.top_mm,
            width_mm=request.width_mm,
        )
        if ink.substituted and not request.allow_substituted_characters:
            listed = ", ".join(
                f"{character!r} (U+{ord(character):04X})" for character in ink.substituted
            )
            raise UsageError(
                subject=f"a reply containing {listed}, which the engraving face cannot draw",
                requirement=(
                    "a reply this face can draw, or allow_substituted_characters to accept a "
                    "struck box on the page for each of them"
                ),
            )
        right_mm = request.left_mm + ink.extent_mm.width_mm
        bottom_mm = request.top_mm + ink.extent_mm.height_mm
        if right_mm > request.screen.width_mm or bottom_mm > request.screen.height_mm:
            raise UsageError(
                subject=(
                    f"a reply reaching {right_mm:.1f}mm by {bottom_mm:.1f}mm on a "
                    f"{request.screen.width_mm:.1f}mm by {request.screen.height_mm:.1f}mm page"
                ),
                requirement=(
                    "a placement and a width that keep the whole reply on the page, since ink "
                    "off the page is ink the human cannot see"
                ),
            )
        return ink

    def _appended(
        self,
        request: ReplyOnPageRequest,
        ink: InkText,
        /,
    ) -> tuple[ScenePrecondition, SceneEdit]:
        """Read the page and fold the ink into its bytes, append-only.

        One read, and the precondition it produced travels with the bytes it describes. Nothing
        re-reads the page in between, because a precondition captured from one read and applied
        to another describes bytes that are not the ones being changed.

        Parameters
        ----------
        request
            The request, for the page's identity.
        ink
            The engraved strokes to add.

        Returns
        -------
        tuple[ScenePrecondition, SceneEdit]
            The identity the write must re-check, and the page's whole new bytes.

        Raises
        ------
        SceneRewriteUnsafe
            The device stores no scene for this page, so there is nothing to amend. Raised here
            rather than by passing empty bytes on, because ``scene is None`` is a state the
            device port models deliberately and fabricating ``b""`` would erase the distinction.
        """
        read = self._writer.read_scene(request.doc_uuid, request.page_id)
        if read.scene is None:
            raise SceneRewriteUnsafe(
                page_uuid=request.page_id,
                detail=(
                    f"the device stores no scene at {read.location}, so there is nothing to "
                    f"append to; this is a page to draw on first, not one to amend"
                ),
            )
        edit = self._appender.append_strokes(
            read.scene,
            request.page_id,
            strokes=ink.strokes,
        )
        return (read.precondition, edit)
