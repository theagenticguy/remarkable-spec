"""The ordering, the non-ASCII refusal, the precondition, and what the result may not claim.

How the three ports are bound here, and why
-------------------------------------------
With local in-memory fakes annotated against the Protocols, exactly as
``test_app_create.py`` binds its three, and for the same reasons: ``rmspec.app`` may import
``rmspec.domain`` and nothing else and these tests hold themselves to the rule their source
obeys; the architecture check only scans ``src/``, so an adapter import here would pass the
gate while breaking the property it protects; and conformance is still checked, by the type
gate, because every fake below is passed to a Protocol-annotated keyword argument.

The fakes carry four seams and nothing more. A shared ``journal``, because this use case *is*
an ordering -- every refusal before the first device call, then one read, one append, one
write -- and an ordering is unassertable without recording the order the collaborators were
called in. ``interference`` on the writer, because "the human drew while you were thinking" is
the property that distinguishes this feature from every neighbouring project and cannot be
reached without a way to move the page between the read and the write. A ``failure`` on the
appender, because ``SceneRewriteUnsafe`` from the encoder must reach the caller unchanged. And
a scripted :class:`InkText` on the engraver, because the substituted characters and the extent
are the two things every refusal here is decided from.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rmspec.app.reply import ReplyOnPage, ReplyOnPageRequest, ReplyOnPageResult
from rmspec.domain._digest import digest_of
from rmspec.domain.errors import (
    DegradationKind,
    DeviceStateMismatchError,
    DeviceUnreachable,
    SceneRewriteUnsafe,
    TransportKind,
    UsageError,
)
from rmspec.domain.models import PAPER_PRO_SCREEN, PenColor, PenType, Point, Stroke
from rmspec.domain.ports.device import (
    _SCENE_FINGERPRINT_TAG,
    ScenePrecondition,
    SceneRead,
    SceneVisibility,
    SceneWriter,
    SceneWriteReceipt,
)
from rmspec.domain.ports.formats import SceneAppender, SceneEdit
from rmspec.domain.ports.render import InkText, InkTextStyle, PhysicalSize, TextEngraver

DOC = "aaaaaaaa-1111-4111-8111-111111111111"
PAGE = "bbbbbbbb-2222-4222-8222-222222222222"

SCENE = b"reMarkable .lines file, version=6          \x00\x01existing ink"
"""Stand-in scene bytes. Nothing in this package decodes them; only their identity matters."""

TAIL = b"\x02the appended reply"
"""What the fake appender adds, so the original stays a strict prefix of the result."""

STYLE = InkTextStyle(em_mm=4.0, line_height=1.3, color=PenColor.BLUE, thickness_scale=1.5)

EM_DASH = "—"
LEFT_QUOTE = "“"
RIGHT_QUOTE = "”"

PAGE_WIDTH_MM = PAPER_PRO_SCREEN.width_mm
PAGE_HEIGHT_MM = PAPER_PRO_SCREEN.height_mm


def _stroke(x: float = 0.0) -> Stroke:
    return Stroke(
        pen=PenType.FINELINER_2,
        color=PenColor.BLUE,
        thickness_scale=1.5,
        points=(Point(x=x, y=0.0), Point(x=x + 4.0, y=6.0)),
    )


def _ink(
    *,
    lines: tuple[str, ...] = ("a reply",),
    substituted: tuple[str, ...] = (),
    width_mm: float = 60.0,
    height_mm: float = 12.0,
    strokes: int = 3,
) -> InkText:
    return InkText(
        strokes=tuple(_stroke(float(index)) for index in range(strokes)),
        lines=lines,
        substituted=substituted,
        extent_mm=PhysicalSize(width_mm=width_mm, height_mm=height_mm),
    )


class _ScriptedEngraver:
    """A :class:`TextEngraver` that returns one prepared :class:`InkText` and records the ask.

    Scripted rather than a real face: the substituted set and the extent are exactly what every
    refusal in this use case is decided from, so a test needs to state them, and a face that
    derived them would make each assertion a test of the face.
    """

    def __init__(
        self,
        ink: InkText | None = None,
        *,
        journal: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, float, float, float]] = []
        self._ink = ink if ink is not None else _ink()
        self._journal = journal if journal is not None else []

    def engrave(
        self,
        text: str,
        /,
        *,
        screen: object,
        style: InkTextStyle,
        left_mm: float,
        top_mm: float,
        width_mm: float,
    ) -> InkText:
        """Record what was asked for, and hand back the prepared ink."""
        self._journal.append("engrave")
        self.screen = screen
        self.style = style
        self.calls.append((text, left_mm, top_mm, width_mm))
        return self._ink


class _AppendingCodec:
    """A :class:`SceneAppender` that concatenates, so the input stays a strict prefix."""

    def __init__(
        self,
        *,
        author_id: int = 2,
        layer_index: int = 1,
        journal: list[str] | None = None,
        failure: SceneRewriteUnsafe | None = None,
    ) -> None:
        self.appended: list[tuple[bytes, str, tuple[Stroke, ...]]] = []
        self._author_id = author_id
        self._layer_index = layer_index
        self._journal = journal if journal is not None else []
        self._failure = failure

    def append_strokes(
        self,
        raw: bytes,
        page_ref: str,
        /,
        *,
        strokes: tuple[Stroke, ...],
    ) -> SceneEdit:
        """Append the ink, or refuse the way the real encoder refuses."""
        self._journal.append("append")
        if self._failure is not None:
            raise self._failure
        if not strokes:
            raise UsageError(subject="no strokes", requirement="ink to append")
        self.appended.append((raw, page_ref, strokes))
        return SceneEdit(
            scene=raw + TAIL,
            author_id=self._author_id,
            layer_index=self._layer_index,
        )


class _MemoryWriter:
    """A :class:`SceneWriter` over one page in memory, with a snapshot per write.

    ``interference`` replaces the stored bytes just before a write re-checks its precondition,
    which is the only way to reach "the human drew while you were thinking" with no device
    attached. The stored scene may be ``None``, which is the blank page of an annotated PDF.
    """

    def __init__(
        self,
        scene: bytes | None = SCENE,
        *,
        interference: bytes | None = None,
        journal: list[str] | None = None,
        failure: DeviceUnreachable | None = None,
    ) -> None:
        self.scene = scene
        self.writes = 0
        self._interference = interference
        self._journal = journal if journal is not None else []
        self._failure = failure
        self._snapshots: dict[str, bytes] = {}

    def read_scene(self, doc_uuid: str, page_id: str, /) -> SceneRead:
        """Hand back the page as it stands, with the identity a write must re-check."""
        self._journal.append("read")
        if self._failure is not None:
            raise self._failure
        return SceneRead(
            doc_uuid=doc_uuid,
            page_id=page_id,
            location=f"/home/root/.local/share/{doc_uuid}/{page_id}.rm",
            scene=self.scene,
        )

    def write_scene(self, precondition: ScenePrecondition, scene: bytes, /) -> SceneWriteReceipt:
        """Replace the page, refusing if it moved since the precondition was captured."""
        self._journal.append("write")
        if self._interference is not None:
            self.scene = self._interference
            self._interference = None
        current = None if self.scene is None else digest_of(_SCENE_FINGERPRINT_TAG, self.scene)
        if current != precondition.fingerprint:
            raise DeviceStateMismatchError(
                transport=TransportKind.SSH,
                subject=precondition.page_id,
                expected=str(precondition.fingerprint),
                observed=str(current),
                retryable=True,
            )
        snapshot = None
        if current is not None and self.scene is not None:
            snapshot = f"/home/root/.rmspec/{precondition.page_id}.{current[:8]}.bak"
            self._snapshots[snapshot] = self.scene
        self.scene = scene
        self.writes += 1
        return SceneWriteReceipt(
            doc_uuid=precondition.doc_uuid,
            page_id=precondition.page_id,
            location=f"/home/root/.local/share/{precondition.doc_uuid}/{precondition.page_id}.rm",
            byte_count=len(scene),
            fingerprint=digest_of(_SCENE_FINGERPRINT_TAG, scene),
            replaced=current,
            snapshot=snapshot,
            visibility=SceneVisibility.REOPEN_REQUIRED,
        )

    def undo(self, receipt: SceneWriteReceipt, /) -> SceneWriteReceipt:
        """Put the superseded scene back, under the same precondition rules."""
        self._journal.append("undo")
        if receipt.snapshot is None:
            raise UsageError(
                subject="a receipt naming no snapshot",
                requirement="a receipt for a write that superseded a scene",
            )
        return self.write_scene(
            ScenePrecondition(
                doc_uuid=receipt.doc_uuid,
                page_id=receipt.page_id,
                fingerprint=receipt.fingerprint,
            ),
            self._snapshots[receipt.snapshot],
        )


def _request(
    *,
    text: str = "a reply",
    left_mm: float = 10.0,
    top_mm: float = 20.0,
    width_mm: float = 80.0,
    allow_substituted_characters: bool = False,
) -> ReplyOnPageRequest:
    return ReplyOnPageRequest(
        doc_uuid=DOC,
        page_id=PAGE,
        text=text,
        screen=PAPER_PRO_SCREEN,
        style=STYLE,
        left_mm=left_mm,
        top_mm=top_mm,
        width_mm=width_mm,
        allow_substituted_characters=allow_substituted_characters,
    )


def _replier(
    *,
    ink: InkText | None = None,
    scene: bytes | None = SCENE,
    interference: bytes | None = None,
    append_failure: SceneRewriteUnsafe | None = None,
    read_failure: DeviceUnreachable | None = None,
    journal: list[str] | None = None,
) -> tuple[ReplyOnPage, _ScriptedEngraver, _AppendingCodec, _MemoryWriter]:
    """Wire one use case over three fakes sharing one journal, and hand all four back."""
    shared = journal if journal is not None else []
    engraver = _ScriptedEngraver(ink, journal=shared)
    appender = _AppendingCodec(journal=shared, failure=append_failure)
    writer = _MemoryWriter(
        scene,
        interference=interference,
        journal=shared,
        failure=read_failure,
    )
    bound_engraver: TextEngraver = engraver
    bound_appender: SceneAppender = appender
    bound_writer: SceneWriter = writer
    return (
        ReplyOnPage(engraver=bound_engraver, appender=bound_appender, writer=bound_writer),
        engraver,
        appender,
        writer,
    )


# ───────────────────────────── the happy path, and its order ─────────────────────────────


def test_a_reply_is_engraved_appended_and_written_in_that_order():
    journal: list[str] = []
    replier, _, appender, writer = _replier(journal=journal)

    result = replier.reply(_request())

    assert journal == ["engrave", "read", "append", "write"]
    assert isinstance(result, ReplyOnPageResult)
    assert writer.scene == SCENE + TAIL
    assert appender.appended[0][0] == SCENE
    assert appender.appended[0][1] == PAGE


def test_the_human_s_existing_ink_is_a_strict_prefix_of_what_was_written():
    # The append-only guarantee, asserted from this layer: whatever else went wrong, no byte of
    # what the human drew can have moved.
    replier, _, _, writer = _replier()

    replier.reply(_request())

    assert writer.scene is not None
    assert writer.scene.startswith(SCENE)
    assert writer.scene != SCENE


def test_the_result_reports_what_was_drawn_and_what_the_appender_decided():
    replier, _, _, _ = _replier(ink=_ink(lines=("first line", "second line"), strokes=7))

    result = replier.reply(_request())

    assert result.lines == ("first line", "second line")
    assert result.stroke_count == 7
    assert result.author_id == 2
    assert result.layer_index == 1
    assert result.extent_mm == PhysicalSize(width_mm=60.0, height_mm=12.0)
    assert result.substituted == ()
    assert result.degradations == ()


def test_the_message_is_stripped_before_it_reaches_the_engraver():
    replier, engraver, _, _ = _replier()

    replier.reply(_request(text="  a reply\n"))

    assert engraver.calls[0][0] == "a reply"


def test_the_placement_reaches_the_engraver_verbatim():
    replier, engraver, _, _ = _replier()

    replier.reply(_request(left_mm=12.5, top_mm=33.0, width_mm=90.0))

    assert engraver.calls[0][1:] == (12.5, 33.0, 90.0)
    assert engraver.screen is PAPER_PRO_SCREEN
    assert engraver.style is STYLE


# ───────────────────────────── the non-ASCII decision ─────────────────────────────


def test_a_reply_the_face_cannot_draw_is_refused_before_the_device_is_touched():
    # The load-bearing property: nobody spends a write and only then discovers the tablet shows
    # boxes. Engraving is local, so the refusal costs nothing and happens before the first read.
    journal: list[str] = []
    replier, _, _, writer = _replier(
        ink=_ink(substituted=(EM_DASH, LEFT_QUOTE)),
        journal=journal,
    )

    with pytest.raises(UsageError) as raised:
        replier.reply(_request(text=f"it is fine {EM_DASH} mostly"))

    assert journal == ["engrave"]
    assert writer.writes == 0
    assert writer.scene == SCENE
    assert "U+2014" in raised.value.message
    assert "U+201C" in raised.value.message
    assert raised.value.remediation is not None
    assert "allow_substituted_characters" in raised.value.remediation


def test_the_refusal_is_the_default_so_a_model_written_reply_cannot_surprise_anyone():
    assert ReplyOnPageRequest.model_fields["allow_substituted_characters"].default is False


def test_opting_in_writes_the_reply_and_records_one_degradation_per_character():
    # The opt-in authorises the write, not the silence: an agent that set the flag once would
    # otherwise draw boxes on every later page with nothing to notice.
    replier, _, _, writer = _replier(ink=_ink(substituted=(EM_DASH, RIGHT_QUOTE)))

    result = replier.reply(
        _request(text=f"fine {EM_DASH} mostly", allow_substituted_characters=True)
    )

    assert writer.writes == 1
    assert result.substituted == (EM_DASH, RIGHT_QUOTE)
    assert [degradation.kind for degradation in result.degradations] == [
        DegradationKind.INK_CHARACTER_SUBSTITUTED,
        DegradationKind.INK_CHARACTER_SUBSTITUTED,
    ]
    assert {degradation.subject for degradation in result.degradations} == {PAGE}
    assert {degradation.substituted for degradation in result.degradations} == {"a struck box"}
    assert "U+2014" in result.degradations[0].detail
    assert "U+201D" in result.degradations[1].detail


def test_nothing_here_folds_a_character_onto_a_lookalike():
    # Folding an em dash to "--" would be a silent edit to somebody's words, and it would be
    # invisible in the result: `substituted` reports what the *face* could not draw, so a folded
    # reply looks exactly like one that needed no folding. The lines are echoed unchanged.
    replier, engraver, _, _ = _replier(
        ink=_ink(lines=(f"fine {EM_DASH} mostly",), substituted=(EM_DASH,))
    )

    result = replier.reply(
        _request(text=f"fine {EM_DASH} mostly", allow_substituted_characters=True)
    )

    assert engraver.calls[0][0] == f"fine {EM_DASH} mostly"
    assert result.lines == (f"fine {EM_DASH} mostly",)
    assert "--" not in result.lines[0]


def test_the_character_refusal_is_decided_before_the_fit_refusal():
    # A caller fixing its characters may change the wrap, so an extent computed from boxes it is
    # about to remove would be advice about a layout that will not exist.
    replier, _, _, _ = _replier(
        ink=_ink(substituted=(EM_DASH,), width_mm=PAGE_WIDTH_MM, height_mm=PAGE_HEIGHT_MM)
    )

    with pytest.raises(UsageError) as raised:
        replier.reply(_request(left_mm=100.0, top_mm=100.0))

    assert "cannot draw" in raised.value.message


# ───────────────────────────── refusals that cost no round trip ─────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_a_whitespace_only_reply_is_refused_before_the_engraver_runs(text: str):
    journal: list[str] = []
    replier, _, _, _ = _replier(journal=journal)

    with pytest.raises(UsageError, match="whitespace"):
        replier.reply(_request(text=text))

    assert journal == []


def test_a_reply_running_off_the_right_edge_is_refused():
    replier, _, _, writer = _replier(ink=_ink(width_mm=100.0, height_mm=10.0))

    with pytest.raises(UsageError) as raised:
        replier.reply(_request(left_mm=PAGE_WIDTH_MM - 50.0, top_mm=0.0))

    assert writer.writes == 0
    assert "off the page" in str(raised.value.remediation)


def test_a_reply_running_below_the_bottom_edge_is_refused():
    # The same defect as the typed text block: ink off the page is ink the human cannot see.
    replier, _, _, writer = _replier(ink=_ink(width_mm=20.0, height_mm=60.0))

    with pytest.raises(UsageError):
        replier.reply(_request(left_mm=0.0, top_mm=PAGE_HEIGHT_MM - 30.0))

    assert writer.writes == 0


def test_a_reply_that_exactly_fills_the_page_is_not_refused():
    # The boundary is inclusive: ink ending on the edge is on the page.
    replier, _, _, writer = _replier(ink=_ink(width_mm=PAGE_WIDTH_MM, height_mm=PAGE_HEIGHT_MM))

    replier.reply(_request(left_mm=0.0, top_mm=0.0))

    assert writer.writes == 1


def test_a_page_with_no_scene_is_refused_rather_than_created():
    # Append-only means there is nothing to append to, and this module does not invent a scene.
    journal: list[str] = []
    replier, _, appender, writer = _replier(scene=None, journal=journal)

    with pytest.raises(SceneRewriteUnsafe) as raised:
        replier.reply(_request())

    assert journal == ["engrave", "read"]
    assert appender.appended == []
    assert writer.writes == 0
    assert raised.value.page_uuid == PAGE
    assert "nothing to" in raised.value.detail


# ───────────────────────── the precondition: refuse, never merge ─────────────────────────


def test_the_human_drawing_between_the_read_and_the_write_refuses_and_writes_nothing():
    # The property that distinguishes this from every neighbouring project. Asserting the error
    # alone would not catch a write that happened anyway, so the stored bytes are asserted too.
    drawn = SCENE + b"\x03the human's own stroke"
    replier, _, _, writer = _replier(interference=drawn)

    with pytest.raises(DeviceStateMismatchError) as raised:
        replier.reply(_request())

    assert writer.scene == drawn
    assert writer.writes == 0
    assert raised.value.retryable is True
    assert raised.value.observed != raised.value.expected


def test_the_precondition_written_with_is_the_one_the_read_produced():
    # Nothing re-reads the page between the read and the write: a precondition captured from one
    # read and applied to another describes bytes that are not the ones being changed.
    journal: list[str] = []
    replier, _, _, writer = _replier(journal=journal)

    result = replier.reply(_request())

    assert journal.count("read") == 1
    assert result.receipt.replaced == digest_of(_SCENE_FINGERPRINT_TAG, SCENE)
    assert writer.scene is not None
    assert result.receipt.fingerprint == digest_of(_SCENE_FINGERPRINT_TAG, writer.scene)


def test_an_encoder_that_will_not_rewrite_the_page_stops_the_write():
    refusal = SceneRewriteUnsafe(
        page_uuid=PAGE,
        detail="re-encoding the unmodified scene produced 18349 byte(s) from 18355",
    )
    replier, _, _, writer = _replier(append_failure=refusal)

    with pytest.raises(SceneRewriteUnsafe) as raised:
        replier.reply(_request())

    assert raised.value is refusal
    assert writer.writes == 0


def test_a_dead_transport_is_never_degraded_into_a_successful_reply():
    replier, _, appender, writer = _replier(
        read_failure=DeviceUnreachable(
            transport=TransportKind.SSH,
            endpoint="10.11.99.1",
            detail="connect refused",
        )
    )

    with pytest.raises(DeviceUnreachable):
        replier.reply(_request())

    assert appender.appended == []
    assert writer.writes == 0


# ───────────────────────── what the result says about the human ─────────────────────────


def test_the_result_never_claims_the_human_can_see_the_reply():
    # An edited page is invisible until the document is reopened, and the writer deliberately
    # does not restart the tablet's UI: four starts in ten minutes reaches a target whose handler
    # reboots the device. The vocabulary has no member that could say otherwise.
    replier, _, _, _ = _replier()

    result = replier.reply(_request())

    assert result.receipt.visibility is SceneVisibility.REOPEN_REQUIRED
    assert {member.value for member in SceneVisibility} == {"reopen_required"}
    assert "already_visible" not in {member.value for member in SceneVisibility}


def test_the_result_carries_the_receipt_whole_so_the_write_can_be_undone():
    # The receipt *is* the undo token. A caller that had to reassemble one from flattened fields
    # could transpose two of them and restore the wrong snapshot over the wrong page.
    replier, _, _, writer = _replier()

    result = replier.reply(_request())
    assert writer.scene == SCENE + TAIL
    reversal = writer.undo(result.receipt)

    assert writer.scene == SCENE
    assert reversal.replaced == result.receipt.fingerprint
    assert result.receipt.snapshot is not None


def test_the_result_names_the_page_once_through_the_receipt():
    # One source for the identifiers rather than an echo alongside them.
    replier, _, _, _ = _replier()

    result = replier.reply(_request())

    assert (result.receipt.doc_uuid, result.receipt.page_id) == (DOC, PAGE)
    assert "doc_uuid" not in ReplyOnPageResult.model_fields
    assert "page_id" not in ReplyOnPageResult.model_fields


def test_no_history_entry_is_appended_because_no_operation_names_a_page_edit():
    # `SyncOperation` has five members and none is an in-place page edit; filing this under
    # `PUSH` would record "a file uploaded to the tablet", which is not what happened. The
    # record that exists is the device-side snapshot, per write.
    parameters = set(ReplyOnPage.__init__.__annotations__)

    assert parameters == {"engraver", "appender", "writer", "return"}
    assert "occurred_at" not in ReplyOnPageRequest.model_fields


# ───────────────────────────── the models themselves ─────────────────────────────


def test_the_request_refuses_a_blank_identifier():
    for doc_uuid, page_id in (("", PAGE), (DOC, "")):
        with pytest.raises(ValidationError):
            ReplyOnPageRequest(
                doc_uuid=doc_uuid,
                page_id=page_id,
                text="a reply",
                screen=PAPER_PRO_SCREEN,
                style=STYLE,
                left_mm=0.0,
                top_mm=0.0,
                width_mm=10.0,
            )


def test_the_request_refuses_a_placement_off_the_top_left_or_a_zero_width_box():
    with pytest.raises(ValidationError):
        _request(left_mm=-1.0)
    with pytest.raises(ValidationError):
        _request(top_mm=-1.0)
    with pytest.raises(ValidationError):
        _request(width_mm=0.0)


def test_the_request_and_the_result_are_frozen_and_forbid_extra_fields():
    request = _request()
    with pytest.raises(ValidationError):
        request.text = "another"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        ReplyOnPageRequest(
            doc_uuid=DOC,
            page_id=PAGE,
            text="a reply",
            screen=PAPER_PRO_SCREEN,
            style=STYLE,
            left_mm=0.0,
            top_mm=0.0,
            width_mm=10.0,
            dry_run=True,  # ty: ignore[unknown-argument]
        )


def test_every_result_field_is_required_so_no_claim_can_be_omitted():
    optional = [
        name for name, field in ReplyOnPageResult.model_fields.items() if not field.is_required()
    ]
    assert optional == []
