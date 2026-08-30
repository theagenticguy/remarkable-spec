"""``rmspec reply``: write a visible ink answer onto a page a human is holding.

The last command, and the only one that changes something a person made by hand. Every decision
here is about not misreporting what happened to it.

Four things this command must not get wrong
-------------------------------------------
**The reply is invisible until the document is reopened.**
:attr:`~rmspec.domain.ports.device.SceneWriteReceipt.visibility` is a deliberately one-member
enum, so no successful reply can claim otherwise: firmware 3.27.3.0 holds an open document's
scene in memory, and the writer deliberately does not restart the tablet's UI to force a redraw
because four starts in ten minutes reaches a target whose handler reboots the device. Every mode
says so -- ``HUMAN`` in the table's caption, ``DENSE`` in a ``visibility`` column, ``JSON`` inside
the receipt and again in ``next.purpose``.

**The write is undoable and the receipt is the token.** ``--json`` carries
:attr:`~rmspec.app.ReplyOnPageResult.receipt` whole, under ``data.receipt``, exactly as the app
layer produced it. It is never flattened into sibling fields, because a caller that reassembles a
receipt can transpose two of them and restore the wrong snapshot over the wrong page -- which is
the result model's own argument for carrying it entire.

**Non-ASCII is refused by default, and nothing folds.** The engraving face draws the 95 printable
ASCII characters. Model-written prose is full of the ones it does not, so this refusal fires
constantly in real use, and ``--allow-substituted-characters`` is the way through: it draws each
undrawable character as a **struck box** and records one degradation per distinct character. It
does not fold anything onto a lookalike -- an em dash does not become ``--`` and a curly quote
does not become a straight one, here or anywhere else in this pipeline, because that is a silent
edit to somebody's words made by the layer with the least right to make one.

**The precondition refuses rather than merges.** If the human draws between the read and the
write, the write is refused, nothing is written, and the failure is
``DeviceStateMismatchError`` with ``retryable=True``. That is the project's differentiator rather
than a fault: the refusal protected their strokes. Re-read, re-compose against the new page, and
decide again.

Why ``--strict`` and ``--allow-substituted-characters`` cannot be given together
-------------------------------------------------------------------------------
:mod:`rmspec.app.reply` argues that the opt-in "authorises the *write*, not the silence", and that
``--strict`` at the shell boundary is what stops an agent that set the flag once from drawing
boxes on every later page with nothing to notice. Two shapes deliver that, and one of them this
CLI may not build.

*Write, record, and exit non-zero* would mean a command choosing an exit status for a run that
succeeded. :func:`~rmspec.domain.errors.exit_code` is documented as "the only place the mapping
exists", precisely so that "a new error class cannot silently acquire exit status 1 by being
forgotten in a command body" -- so a status invented in this module is the thing that docstring
exists to prevent, and there is no domain error to score for an outcome that is not a failure.

*Refuse the combination* keeps the status in the domain's hands and is louder rather than quieter:
the caller is told, before anything is written, that it asked both to proceed-and-record and to
refuse-rather-than-record. So the two are mutually exclusive, like ``--json`` and ``--dense``, and
a caller picks. Without ``--strict``, the opt-in works and the degradations are reported in every
mode; with ``--strict`` and no opt-in, the use case's own ``UsageError`` names every undrawable
character with its code point before the first device call.

Placement is millimetres from the page's top-left
-------------------------------------------------
No flag mentions ``x_shift``. The centre-origin correction that scene ``x`` needs is half a page
width, and the engraver applies it inside -- so a caller measures from the corner of the sheet it
is looking at, which is the only frame a person holding a page has. ``--width-mm`` is a wrap
width and the box does not grow sideways; its height is whatever the wrapped text needs, and a
reply that would run off the bottom is refused before the tablet is touched.
"""

from __future__ import annotations

import sys
from shlex import quote
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from rich.table import Table

from rmspec.app import ReplyOnPage, ReplyOnPageRequest
from rmspec.cli._invoke import (
    FEATURE_DEVICE_SSH,
    FEATURE_SCENE_DECODE,
    DenseFlag,
    Invoked,
    JsonFlag,
    StrictFlag,
    run,
)
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode
from rmspec.domain.errors import UsageError
from rmspec.domain.models import PAPER_PRO_SCREEN, PenColor
from rmspec.domain.ports.render import InkTextStyle

if TYPE_CHECKING:
    from rmspec.app import ReplyOnPageResult

__all__ = [
    "DEFAULT_COLOUR",
    "DEFAULT_EM_MM",
    "DEFAULT_LEFT_MM",
    "DEFAULT_LINE_HEIGHT",
    "DEFAULT_TOP_MM",
    "DEFAULT_WIDTH_MM",
    "DENSE_COLUMNS",
    "HUMAN_COLUMNS",
    "REOPEN_SENTENCE",
    "reply",
]

DEFAULT_LEFT_MM: Final = 15.0
"""Left edge of the reply's box, in millimetres from the page's left edge.

A margin rather than the edge itself, because ink at ``0.0`` sits against the bezel where a
Paper Pro's own writing never does.
"""

DEFAULT_TOP_MM: Final = 15.0
"""Top edge of the reply's box, in millimetres from the page's top edge."""

DEFAULT_WIDTH_MM: Final = 150.0
"""Wrap width in millimetres, which leaves a matching right margin on a 179.7mm page."""

DEFAULT_EM_MM: Final = 5.0
"""Height of one em in millimetres. Roughly the size a person writes at on this screen."""

DEFAULT_LINE_HEIGHT: Final = 1.4
"""Baseline-to-baseline distance as a multiple of the em."""

DEFAULT_COLOUR: Final = PenColor.BLUE
"""Ink colour.

Not black, deliberately. The domain's own field docstring says the colour exists "so a reply is
as visible as the human's own ink and can be told from it at a glance", and a black reply on a
page of black handwriting fails the second half of that.
"""

REOPEN_SENTENCE: Final = (
    "the reply is on the tablet and not yet on the screen: close the document and reopen it, "
    "because the firmware holds an open document's scene in memory and nothing here restarts "
    "the tablet to force a redraw"
)
"""The one sentence every mode has to carry. See this module's docstring for why."""

DENSE_COLUMNS: Final = (
    "doc_uuid",
    "page_id",
    "lines",
    "strokes",
    "width_mm",
    "height_mm",
    "substituted",
    "snapshot",
    "visibility",
)
"""What ``--dense`` projects: the write's identity, what landed, and what the human can see.

``author_id`` and ``layer_index`` are in ``--json`` and deliberately not here. The result model
says both are "a fact about these bytes only" -- the tablet renumbers scene ids across its own
re-save -- so a column inviting a caller to key on one would invite the bug the port warns about.
"""

HUMAN_COLUMNS: Final = (1, 2, 3, 5, 6, 8)
"""Indices into the same row :data:`DENSE_COLUMNS` labels, which is what stops the two drifting.

Six of nine. The document uuid goes because the person just typed the document's name; the width
goes because they chose it; ``snapshot`` goes because it is an undo token for a machine and a
person takes a reply back on the tablet.
"""

PageOption = Annotated[str, Parameter(name="--page")]
"""``--page``: which page to write on, as the device identifies it.

Required, and a page **identifier** rather than an index. Resolving an index would need a second
port -- the document's own page order -- and a command reaching for two ports is a command doing
two jobs. Every payload that lists pages already carries the identifier: ``rmspec render DOC OUT
--json`` and ``rmspec ocr DOC --json`` both report ``page_id`` per page.
"""

ColourOption = Annotated[PenColor | None, Parameter(name=("--colour", "--color"))]
"""``--colour``: the pen colour the reply is minted with, from the tablet's own fourteen.

Optional with the default resolved in the body rather than in the signature, which is the
shape ``--thickness`` already has and for a related reason. ``PenColor`` is an ``IntEnum``,
so ``_jsonable`` in the manifest renders a member as its **number** while cyclopts renders
the choices as kebab-cased **names** -- a manifest saying ``default: 6`` next to
``choices: [..., "blue", ...]`` would be advertising a default the CLI itself rejects. A
``None`` default says "unset" in both vocabularies and contradicts neither, and
:data:`DEFAULT_COLOUR` stays the single place the choice is made.
"""

AllowSubstitutedFlag = Annotated[
    bool,
    Parameter(name="--allow-substituted-characters", negative=""),
]
"""``--allow-substituted-characters``: draw a struck box for each character the face cannot draw.

``negative=""`` for the reason :data:`~rmspec.cli._invoke.JsonFlag` gives: cyclopts would
otherwise generate a ``--no-`` form that neither ``--help`` nor the manifest lists.
"""


def _message(text: str | None, /) -> str:
    """Take the reply from the argument if there is one, and from stdin otherwise.

    Parameters
    ----------
    text
        The positional ``TEXT``, or ``None`` when it was not given.

    Returns
    -------
    str
        The message as the caller wants it read, unmodified.

    Raises
    ------
    UsageError
        No ``TEXT`` was given and stdin is a terminal, so there is nothing to read and reading
        would hang waiting for a person to type a paragraph and press ctrl-D.

    Notes
    -----
    Stdin is the primary path, not the fallback. A reply is prose, and prose in a shell argument
    is what quoting mangles -- an apostrophe ends the string, a newline is lost, and the
    substitution report then names characters the caller never typed. An agent piping a paragraph
    in is the caller this command is for.

    Nothing is stripped here. :meth:`~rmspec.app.ReplyOnPage.reply` strips and refuses a
    whitespace-only reply itself, and its error is deliberately about the number of characters
    that *were* typed, which only the unstripped string knows.
    """
    if text is not None:
        return text
    if sys.stdin.isatty():
        raise UsageError(
            subject="a reply with no TEXT argument and a terminal on stdin",
            requirement=(
                "the message as a TEXT argument, or piped on stdin as in "
                "printf '%s' \"...\" | rmspec reply DOC --page PAGE"
            ),
        )
    return sys.stdin.read()


def _row(result: ReplyOnPageResult, /) -> tuple[str, ...]:
    """Project one reply into the single record every non-JSON mode renders.

    Parameters
    ----------
    result
        What the use case reported.

    Returns
    -------
    tuple[str, ...]
        One cell per :data:`DENSE_COLUMNS` entry, in that order.

    Notes
    -----
    ``"-"`` marks a cell with nothing in it rather than an empty string, so a ``DENSE`` consumer
    can tell "no characters were substituted" from a column it mis-cut, and so the ``HUMAN``
    table -- which reads the same strings by index -- has no blank rows in it.
    """
    return (
        result.receipt.doc_uuid,
        result.receipt.page_id,
        str(len(result.lines)),
        str(result.stroke_count),
        f"{result.extent_mm.width_mm:.1f}",
        f"{result.extent_mm.height_mm:.1f}",
        ",".join(result.substituted) or "-",
        result.receipt.snapshot or "-",
        result.receipt.visibility.value,
    )


def _table(row: tuple[str, ...], /) -> Table:
    """Render one reply for a person, as a narrower projection of the dense record.

    Parameters
    ----------
    row
        The record :func:`_row` built.

    Returns
    -------
    Table
        Field-and-value pairs for the :data:`HUMAN_COLUMNS` indices, captioned with the one
        sentence about visibility that a person must not miss.

    Notes
    -----
    Read **by index into the same tuple**, which is the rule §8.2 of the design states and the
    reason a nine-column record and a six-row table cannot come to disagree: there is one source
    of strings and two views of it.
    """
    table = Table(title="reply written", caption=REOPEN_SENTENCE, caption_justify="left")
    table.add_column("field")
    table.add_column("value", overflow="fold")
    for index in HUMAN_COLUMNS:
        table.add_row(DENSE_COLUMNS[index], row[index])
    return table


def _next(doc: str, result: ReplyOnPageResult, /) -> NextAction:
    """Name the command that shows what is now on the page.

    Parameters
    ----------
    doc
        The selector the caller typed, quoted so a document called ``Quick sheets`` survives.
    result
        The reply, for the page the render should be checked against.

    Returns
    -------
    NextAction
        A literal shell line, because that field is documented as "the exact shell line to run,
        ready to execute. Never a paraphrase of one."

    Notes
    -----
    Rendering is the honest next command, and the reopen instruction rides in ``purpose`` rather
    than in ``command``. Reopening a document is something a person does with their hands: there
    is no shell line for it, and putting prose where a command belongs would break the one rule
    that field has. So ``command`` reads the page back from the tablet -- where the reply already
    is -- and ``purpose`` says why the tablet itself still shows the page without it.
    """
    return NextAction(
        command=f"rmspec render {quote(doc)} reply-{result.receipt.page_id}.svg",
        purpose=(
            f"render the page back from the tablet to see the reply that is stored on it; "
            f"{REOPEN_SENTENCE}"
        ),
    )


def reply(
    doc: str,
    text: str | None = None,
    /,
    *,
    page: PageOption,
    left_mm: float = DEFAULT_LEFT_MM,
    top_mm: float = DEFAULT_TOP_MM,
    width_mm: float = DEFAULT_WIDTH_MM,
    em_mm: float = DEFAULT_EM_MM,
    line_height: float = DEFAULT_LINE_HEIGHT,
    colour: ColourOption = None,
    thickness: float | None = None,
    allow_substituted_characters: AllowSubstitutedFlag = False,
    strict: StrictFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Write an ink reply onto one page of a document on the attached tablet.

    The reply lands in the tablet's store immediately and appears on screen only after the
    document is closed and reopened -- the firmware holds an open document's scene in memory and
    nothing here restarts the tablet to force a redraw. The write is atomic, it keeps a snapshot
    of what it replaced, and it is **refused** rather than merged if the page changed since it was
    read, so a stroke the human drew in between is never lost. Placement is in millimetres from
    the page's top-left corner.

    Parameters
    ----------
    doc
        The document to write into: a name substring, a uuid, or a uuid prefix.
    text
        The message, as you want it read. Omit it to read the whole of stdin instead, which is
        the path a paragraph should take -- shell quoting mangles prose, and the substitution
        report would then name characters you never typed.
    page
        The page to write on, as the device identifies it. ``rmspec render DOC OUT --json`` and
        ``rmspec ocr DOC --json`` both report ``page_id`` for every page they touch.
    left_mm
        Left edge of the text box, in millimetres from the page's left edge.
    top_mm
        Top edge of the text box, in millimetres from the page's top edge. The first baseline
        sits one em below it.
    width_mm
        Wrap width in millimetres. Lines wrap inside it and the box does not grow sideways; its
        height is whatever the wrapped text needs, and a reply that would run off the page is
        refused before the tablet is touched.
    em_mm
        Height of one em in millimetres, which is the size knob.
    line_height
        Baseline-to-baseline distance as a multiple of ``em_mm``.
    colour
        Ink colour, from the tablet's own fourteen, named rather than numbered -- ``blue``,
        ``gray-overlap``. Blue when unset, so the reply can be told apart from black
        handwriting at a glance.
    thickness
        The tablet's thickness-slider value the strokes are minted with. Defaults to
        ``RMSPEC_THICKNESS``, so one setting calibrates rendering and replying together.
    allow_substituted_characters
        Write a reply the engraving face cannot fully draw. The face has the 95 printable ASCII
        characters and nothing else, so an em dash, a curly quote, an ellipsis or a typographic
        apostrophe -- all of which model-written prose is full of -- is drawn as a **struck box**
        on the page. Nothing is folded onto a lookalike and nothing is dropped: this flag accepts
        a visible box wherever each character appeared, and records one degradation per distinct
        character. Without it, such a reply is refused before the tablet is touched, with every
        offending character named by code point.
    strict
        Refuse anything the default would merely record: an ambiguous ``DOC`` becomes
        ``AmbiguousDocument`` instead of the ranked winner. Cannot be combined with
        ``--allow-substituted-characters``, because one asks to proceed and record while the
        other asks to refuse rather than record, and picking a winner silently is how a page ends
        up full of boxes the caller thought it had forbidden.
    json
        Emit the one envelope on stdout. ``data`` is the whole ``ReplyOnPageResult``, including
        ``receipt`` unflattened -- that value is the undo token, and reassembling it from
        separate fields is how two of them get transposed and the wrong snapshot is restored
        over the wrong page.
    dense
        Emit one tab-separated record on stdout instead.

    Returns
    -------
    int
        ``0``.
    """

    def body(invoked: Invoked) -> int:
        if strict and allow_substituted_characters:
            raise UsageError(
                subject="--strict together with --allow-substituted-characters",
                requirement=(
                    "one of them: --allow-substituted-characters draws a struck box and records "
                    "a degradation, --strict refuses what would only be recorded"
                ),
            )
        invoked.probe(FEATURE_SCENE_DECODE, FEATURE_DEVICE_SSH)
        resolved = invoked.document(doc, strict=strict)
        result = invoked.get(ReplyOnPage).reply(
            ReplyOnPageRequest(
                doc_uuid=resolved.chosen.uuid,
                page_id=page,
                text=_message(text),
                screen=PAPER_PRO_SCREEN,
                style=InkTextStyle(
                    em_mm=em_mm,
                    line_height=line_height,
                    color=DEFAULT_COLOUR if colour is None else colour,
                    thickness_scale=(
                        invoked.settings.thickness if thickness is None else thickness
                    ),
                ),
                left_mm=left_mm,
                top_mm=top_mm,
                width_mm=width_mm,
                allow_substituted_characters=allow_substituted_characters,
            )
        )
        degradations = (*resolved.degradations, *result.degradations)
        row = _row(result)
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=RESPONSE_TYPES["reply"],
                degradations=degradations,
                next_action=_next(doc, result),
            )
        elif invoked.out.mode is OutputMode.DENSE:
            invoked.report(degradations)
            invoked.out.rows(DENSE_COLUMNS, (row,))
        else:
            invoked.report(degradations)
            invoked.out.display(_table(row))
        return 0

    return run(body, json=json, dense=dense)
