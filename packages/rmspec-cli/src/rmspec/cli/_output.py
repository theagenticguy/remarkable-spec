r"""stdout is for the machine; stderr is for the human. One rule, applied in one place.

The legacy tree had eleven module-level ``Console()`` objects, every one of them on stdout
and not one of them on stderr. That is a single cause with six symptoms: four ``--json``
modes emitted progress prose before their JSON and so could not be piped, and two more
corrupted the JSON itself. Fixing it per command would be whack-a-mole, so it is fixed as a
rule here and every command inherits it.

The two streams, and what each is for
-------------------------------------
**stdout carries the machine-consumable payload and nothing else** -- the JSON envelope
:meth:`CliOutput.emit` writes, the tab-separated records :meth:`CliOutput.rows` writes, or
the shell assignments :meth:`CliOutput.line` writes for ``rmspec env``. **stderr carries
everything a human reads**: tables, warnings, degradation summaries, error prose. So
``rmspec doctor --json | jq`` is clean by construction, and ``rmspec doctor 2>/dev/null`` on
a human run is silent, which is the correct signal that nothing machine-readable was
requested.

Three modes, not two
--------------------
:class:`OutputMode` is one value, so a command cannot be in two modes at once and no caller
has to reconcile two independent booleans. ``HUMAN`` is the default and keeps its tables on
stderr; ``JSON`` writes one envelope on stdout for an agent that parses; ``DENSE`` writes
tab-separated records on stdout for an agent that greps, or for a bounded context window.
``--json`` and ``--dense`` are mutually exclusive and :func:`resolve_mode` is the one place
that says so.

Why ``print_json`` and never ``console.print(json.dumps(...))``
--------------------------------------------------------------
Measured, on a payload whose value contains ``[draft]``, against a 20-column console::

    console.print(json.dumps(payload))
    # '{"name": "Notes  \n2026 architecture \nreview with a very \nlong trailing \ntitle", ...'

Two independent defects in one line. ``rich`` parsed ``[draft]`` as console markup and
deleted it, and it hard-wrapped the string at the console width -- which off a tty is 80
columns, not the terminal's. The result is not JSON. ``print_json`` builds a ``JSON``
renderable and prints it with ``soft_wrap=True``, so the same payload round-trips through
``json.loads`` unchanged with ``[draft]`` intact.

That is necessary and not sufficient. ``rich`` honours ``FORCE_COLOR``, which is set to
``3`` in at least one environment this project is developed in, and a ``Console`` whose file
is a pipe then emits SGR escapes into it anyway -- which also is not JSON. So the data
console is built with ``force_terminal=False`` and ``no_color=True``, and
:meth:`CliOutput.emit` passes ``highlight=False``. Both consoles are built with
``markup=False``, so a document named ``Notes [draft]`` survives a human table too.

``DENSE`` goes through the plain writer for the same reasons and one more: ``rich`` would
wrap a long cell onto a second line, and a format whose entire contract is "one record per
line" cannot survive that.

The envelope has a fixed frame and a payload that cannot collide with it
-----------------------------------------------------------------------
Every success document is ``{api_version, type, data, degradations}`` plus an optional
``next``; every failure is ``{api_version, type: "error", error}`` plus an optional ``next``.
A reader branches on ``type`` before it touches ``data``, which is the whole point of a
discriminator. Because the command's own result always goes **under** ``data``, a result key
called ``next`` or ``type`` is no longer a collision -- it is just a key. That is a real
improvement over the flat shape this replaces, where the payload's keys *were* the
document's top-level keys and :data:`NEXT_ACTION_KEY` had to be reserved by convention and
policed by a test. The reserved names are still named, in
:data:`RESERVED_ENVELOPE_KEYS`, because a command author reading this module should be able
to see the frame -- not because a payload can break it.

The error envelope renders what the domain already carries
---------------------------------------------------------
:class:`~rmspec.domain.errors.RmspecError`-shaped values already have ``code``, ``message``
and ``remediation``, and :class:`~rmspec.domain.errors.AmbiguousDocument` already carries
:class:`~rmspec.domain.errors.DocumentCandidate`. So the error document invents no
vocabulary: it is a projection. ``candidates`` is present only when the failure has them,
because an empty list would say "we looked and found none" about every error that never
looks.

The next-action hint
--------------------
:class:`NextAction` carries the **literal command to run**, not a description of it. That is
the cheapest thing that makes a surface legible to an agent: a caller that can read
``"command": "uv sync --extra render"`` out of a failure can act on it without a human
translating a sentence into a shell line.

Exit status comes from the domain
---------------------------------
:meth:`CliOutput.fail` returns :func:`~rmspec.domain.errors.exit_code`'s answer and does not
choose one. Legacy had exactly two statuses, 0 and 1, across roughly sixty ``sys.exit(1)``
call sites -- and its ``sync pull`` exited 0 while reporting skipped documents. The same
number is also carried **inside** the failure document, because that costs nothing and saves
an agent a second channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, Field
from rich.console import Console

from rmspec.domain.errors import AmbiguousDocument, DocumentCandidate, UsageError, exit_code

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from typing import TextIO

    from rich.console import RenderableType

    from rmspec.domain.errors import Degradation, RmspecError

__all__ = [
    "API_VERSION",
    "API_VERSION_KEY",
    "DATA_KEY",
    "DEGRADATIONS_KEY",
    "ERROR_KEY",
    "ERROR_RESPONSE_TYPE",
    "NEXT_ACTION_KEY",
    "RESERVED_ENVELOPE_KEYS",
    "TYPE_KEY",
    "CliOutput",
    "ConsolePair",
    "ErrorEnvelope",
    "NextAction",
    "OutputMode",
    "make_console_pair",
    "open_output",
    "resolve_mode",
]

API_VERSION = "rmspec/v1"
"""The value of every document's ``api_version`` key.

One string, in one place, so that the day the envelope's frame changes incompatibly there is
exactly one line to bump and one constant for a consumer's test to pin against.
"""

API_VERSION_KEY = "api_version"
"""Where :data:`API_VERSION` goes. Always present, on success and on failure alike."""

TYPE_KEY = "type"
"""Where the response discriminator goes. Always present; a reader branches on it first."""

DATA_KEY = "data"
"""Where a successful command's own result goes, whole and unflattened."""

DEGRADATIONS_KEY = "degradations"
"""Where the run's degradations go, hoisted out of ``data`` to one predictable place.

All twelve use-case results carry ``degradations: tuple[Degradation, ...]``, so an agent
should look in one place rather than learn twelve. Always present, ``[]`` when there were
none, order-preserving and never deduplicated -- collapsing duplicates is a presentation
choice and belongs in :attr:`OutputMode.HUMAN` only.
"""

ERROR_KEY = "error"
"""Where an :class:`ErrorEnvelope` goes. Present on failure documents and only those."""

ERROR_RESPONSE_TYPE = "error"
"""The :data:`TYPE_KEY` value every failure carries, whichever command failed."""

NEXT_ACTION_KEY = "next"
"""Where a :class:`NextAction` goes, on the documents that have an obvious next command.

The one optional key in the frame. Absent rather than ``null`` when there is nothing to
suggest, because "no obvious next step" is not a next step whose command is unknown.
"""

RESERVED_ENVELOPE_KEYS = frozenset(
    {API_VERSION_KEY, TYPE_KEY, DATA_KEY, DEGRADATIONS_KEY, ERROR_KEY, NEXT_ACTION_KEY}
)
"""Every key the envelope's frame owns, so a command author can see the whole frame at once.

Documentation, not a guard. A command's result cannot collide with these because it is
nested under :data:`DATA_KEY` rather than merged into the top level, so ``data`` may itself
contain a key called ``next`` and nothing ambiguous happens.
"""

_DENSE_CELL_ESCAPES = str.maketrans({"\t": " ", "\n": " ", "\r": " "})
"""Tab, newline and carriage return each become one space inside a ``DENSE`` cell.

Deliberately lossy. The entire value of the format is that ``cut -f2`` works on it, so a
literal tab inside a cell must not survive; by the same argument a newline inside a cell
must not survive either, because "one record per line" is the other half of the contract and
transcribed handwriting is full of them. Escaping to ``\\t`` would be reversible and is the
wrong trade: it makes every consumer un-escape before using the value, which is precisely
the work ``--json`` already does properly.
"""


class OutputMode(StrEnum):
    """Which of the three renderings a command was asked for. Exactly one, always.

    A ``StrEnum`` rather than two booleans, because two booleans have four states and only
    three of them are meaningful. Making the illegal fourth state unrepresentable is why
    a command can branch on this once and never re-check.
    """

    HUMAN = "human"
    """Tables and prose on stderr, for a person. The default."""

    JSON = "json"
    """One envelope on stdout, for an agent that parses. Selected by ``--json``."""

    DENSE = "dense"
    """Tab-separated records on stdout, for an agent that greps. Selected by ``--dense``."""


def resolve_mode(*, json: bool, dense: bool) -> OutputMode:
    """Turn the two flags a command parses into the one mode it renders in.

    Parameters
    ----------
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.

    Returns
    -------
    OutputMode
        :attr:`OutputMode.JSON`, :attr:`OutputMode.DENSE`, or :attr:`OutputMode.HUMAN` when
        neither flag was given.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        When both flags were passed. The exclusivity is checked once, here, rather than in
        each command -- and it is a domain ``UsageError`` so that it exits 2 and renders
        through :meth:`CliOutput.fail` like every other failure, instead of being a special
        case the CLI has to describe itself.
    """
    if json and dense:
        raise UsageError(
            subject="--json and --dense",
            requirement="at most one of them, because a run has exactly one output mode",
        )
    if json:
        return OutputMode.JSON
    if dense:
        return OutputMode.DENSE
    return OutputMode.HUMAN


def open_output(
    consoles: ConsolePair,
    /,
    *,
    json: bool,
    dense: bool,
) -> tuple[CliOutput, UsageError | None]:
    """Build one invocation's writer, and the mode complaint if the flags contradict.

    Every command begins with these two lines, which is the whole reason this exists::

        out, refusal = open_output(consoles, json=json, dense=dense)
        if refusal is not None:
            return out.fail(refusal)

    Parameters
    ----------
    consoles
        The process's two consoles.
    json
        Whether ``--json`` was passed.
    dense
        Whether ``--dense`` was passed.

    Returns
    -------
    tuple[CliOutput, UsageError | None]
        A writer that is always usable, and the refusal to render through it, or ``None``.

    Notes
    -----
    :func:`resolve_mode` raises, and a raised mode error is awkward at exactly one place: the
    writer that would render it is the thing that could not be built. Returning the pair
    instead means the failure path is the same two lines every command already has, rather
    than a ``try`` around the construction of the object the ``except`` needs.

    When both flags are passed the writer is built in :attr:`OutputMode.JSON` if ``--json``
    was among them. A caller who asked for JSON at all is more likely to be parsing than
    reading, so a machine-readable refusal serves it better than a sentence -- and guessing
    *some* mode is unavoidable, since the caller named two.
    """
    try:
        mode = resolve_mode(json=json, dense=dense)
    except UsageError as refusal:
        fallback = OutputMode.JSON if json else OutputMode.HUMAN
        return CliOutput(consoles=consoles, mode=fallback), refusal
    return CliOutput(consoles=consoles, mode=mode), None


class NextAction(BaseModel, frozen=True, extra="forbid"):
    """The literal command a caller should run next, and why.

    A description of a next step is a sentence an agent has to translate; a command is a
    command. Both fields are non-empty, because "run something" and "for some reason" are
    each worse than saying nothing.
    """

    command: str = Field(min_length=1)
    """The exact shell line to run, ready to execute. Never a paraphrase of one."""

    purpose: str = Field(min_length=1)
    """What running it accomplishes, for the human reading over the agent's shoulder."""


class ErrorEnvelope(BaseModel, frozen=True, extra="forbid"):
    """A failure in the shape ``--json`` promises, projected from the domain's own fields."""

    type: str = Field(min_length=1)
    """:attr:`~rmspec.domain.errors.RmspecError.code` -- the class name, which is stable.

    That name **is** the machine identity, and no second vocabulary is invented. A parallel
    ``ERR_*`` enum would be a hand-maintained mirror of a 34-class tree that already has
    stable names, and the first time someone added an error the two would disagree. What
    that pattern is actually for -- an agent discovering the closed set of identities it may
    have to branch on -- is served by ``rmspec manifest``, which enumerates every error
    class with its exit code.
    """

    message: str = Field(min_length=1)
    """The assembled sentence the domain built from its structured fields."""

    remediation: str | None
    """The next thing to do, when the error names one. ``None`` is rendered as ``null``.

    No default, so a construction site states it. Only 9 of 34 error classes supply one, and
    present-but-null and absent are different claims: a caller checking
    ``.error.remediation`` should not have to handle both.
    """

    exit_code: int
    """The status the process will exit with, from :func:`~rmspec.domain.errors.exit_code`.

    Carried inside the document as well as returned to the shell. The domain resolves it by
    an MRO walk over a deliberately sparse private table, so an agent cannot derive it from
    :attr:`type` without reimplementing that walk -- and duplicating it here costs one
    integer and saves a second channel.
    """

    candidates: tuple[DocumentCandidate, ...] | None = None
    """Documents that matched an ambiguous query, or ``None`` when the failure has none.

    ``None`` rather than an empty tuple, and dropped from the document entirely, because an
    empty list would claim "we searched and found nothing" about every failure that never
    searched.
    """

    @classmethod
    def of(cls, err: RmspecError, /) -> Self:
        """Project one domain error into the envelope.

        Parameters
        ----------
        err
            The failure that ended the run.

        Returns
        -------
        Self
            The envelope. ``candidates`` is populated only for
            :class:`~rmspec.domain.errors.AmbiguousDocument`, which is the one error in the
            tree that carries them -- tested by ``isinstance`` rather than by probing for
            an attribute, so the type checker can see that the field exists.
        """
        return cls(
            type=err.code,
            message=err.message,
            remediation=err.remediation,
            exit_code=exit_code(err),
            candidates=err.candidates if isinstance(err, AmbiguousDocument) else None,
        )

    def as_document(self) -> dict[str, Any]:
        """Render the envelope as the mapping that goes under the ``error`` key.

        Returns
        -------
        dict[str, Any]
            JSON-ready. ``candidates`` is omitted when there are none rather than emitted
            as ``null``.
        """
        document = self.model_dump(mode="json")
        if self.candidates is None:
            del document["candidates"]
        return document


@dataclass(frozen=True, slots=True)
class ConsolePair:
    """The process's two consoles and its one plain writer, resolved once and injected.

    ``Scope.APP``: streams cannot change inside a run. Constructing these at module import
    is what made the legacy tree's eleven consoles impossible to point somewhere else, so
    nothing here is built at import time and nothing here is global.
    """

    data: Console
    """stdout, configured so that what lands on it is always parseable."""

    human: Console
    """stderr, where every table, warning and error sentence goes."""

    plain: TextIO
    """stdout again, with no ``rich`` in the way at all.

    ``rmspec env`` writes here, and so does every ``DENSE`` record.
    ``eval "$(rmspec env)"`` breaks on any path over about 65 characters if a ``Console``
    touches it, because off a tty ``rich`` wraps at 80 columns and splits an assignment
    across lines -- and it eats ``[...]`` in a path as markup. A wrapped tab-separated
    record fails for exactly the same reason.
    """


def make_console_pair(*, stdout: TextIO, stderr: TextIO) -> ConsolePair:
    """Build the process's consoles over two given streams.

    Parameters
    ----------
    stdout
        Stream for machine-consumable output. Passed in, so a test does not have to
        capture the real one and a caller can redirect deliberately.
    stderr
        Stream for everything a human reads.

    Returns
    -------
    ConsolePair
        The data console, the human console, and ``stdout`` again as the plain writer.

    Notes
    -----
    ``force_terminal=False`` and ``no_color=True`` on the data console are not belt and
    braces: ``rich`` reads ``FORCE_COLOR`` from the environment and will write SGR escapes
    into a pipe on the strength of it, which turns valid JSON into something ``jq`` rejects.
    ``markup=False`` on both is why a document named ``Notes [draft]`` keeps its name.
    """
    return ConsolePair(
        data=Console(
            file=stdout,
            markup=False,
            emoji=False,
            highlight=False,
            no_color=True,
            force_terminal=False,
        ),
        human=Console(
            file=stderr,
            markup=False,
            emoji=False,
            highlight=False,
        ),
        plain=stdout,
    )


def _dense_record(cells: Sequence[str], /) -> str:
    r"""Join one record's cells with tabs, having escaped the tabs and newlines inside them.

    Parameters
    ----------
    cells
        One record's already-stringified values.

    Returns
    -------
    str
        The record, without its trailing newline. ``\r\n`` collapses to one space rather
        than two, so a cell carrying Windows line endings does not silently widen.
    """
    return "\t".join(cell.replace("\r\n", "\n").translate(_DENSE_CELL_ESCAPES) for cell in cells)


class CliOutput:
    """The one thing a command writes through, in one of three modes.

    ``Scope.REQUEST``: :attr:`mode` comes from the invocation's flags, so it is per-command,
    while the :class:`ConsolePair` behind it is per-process.

    Notes
    -----
    A command branches once, at the top, and never again::

        if out.mode is OutputMode.JSON:
            out.emit(result.model_dump(mode="json"), response_type="catalog",
                     degradations=result.degradations)
        elif out.mode is OutputMode.DENSE:
            out.rows(("uuid", "name"), ((d.uuid, d.name) for d in result.documents))
        else:
            out.report_degradations(result.degradations)
            out.display(table)
        return 0
    """

    def __init__(self, *, consoles: ConsolePair, mode: OutputMode) -> None:
        self._consoles = consoles
        self._mode = mode

    @property
    def mode(self) -> OutputMode:
        """Report which rendering the caller asked for.

        Returns
        -------
        OutputMode
            The one mode this invocation runs in. The only branch a command needs.
        """
        return self._mode

    @property
    def machine_readable(self) -> bool:
        """Report whether the caller asked for the JSON envelope specifically.

        Returns
        -------
        bool
            ``True`` only in :attr:`OutputMode.JSON`. ``DENSE`` is also machine-consumable,
            but it is a different document with different keys, so a caller that means "emit
            the envelope" must not be told yes for it. Kept as a property over the enum so
            that no existing call site changed meaning when the third mode arrived.
        """
        return self._mode is OutputMode.JSON

    def emit(
        self,
        payload: Mapping[str, Any],
        /,
        *,
        response_type: str,
        degradations: Sequence[Degradation] = (),
        next_action: NextAction | None = None,
    ) -> None:
        """Write one JSON envelope to stdout.

        Parameters
        ----------
        payload
            The command's result as a JSON-ready mapping. It goes under
            :data:`DATA_KEY` whole, so its keys are never the document's top-level keys and
            can never collide with the frame.
        response_type
            The discriminator for :data:`TYPE_KEY` -- ``"catalog"``, ``"transcription"``,
            and so on. Named for the payload's shape rather than the verb, because two
            commands may one day return the same shape.
        degradations
            The tuple the use-case result carries. Serialised item by item and always
            emitted, as ``[]`` when empty.
        next_action
            The literal command to run next, when there is an obvious one.

        Notes
        -----
        Snake_case throughout, and the reasoning is not the implementer's to re-decide:
        "The skill this pattern comes from writes ``apiVersion``, which is TypeScript's
        convention; every key inside ``data`` here is snake_case because the app layer's
        result models are, and one document mixing ``apiVersion`` with ``page_index`` is
        worse than either convention applied consistently."

        ``print_json`` rather than ``console.print(json.dumps(...))``: see this module's
        docstring for the measurement. ``highlight=False`` keeps the escape codes out even
        when ``FORCE_COLOR`` is set.
        """
        document: dict[str, Any] = {
            API_VERSION_KEY: API_VERSION,
            TYPE_KEY: response_type,
            DATA_KEY: dict(payload),
            DEGRADATIONS_KEY: [item.model_dump(mode="json") for item in degradations],
        }
        if next_action is not None:
            document[NEXT_ACTION_KEY] = next_action.model_dump(mode="json")
        self._consoles.data.print_json(data=document, highlight=False)

    def rows(self, header: Sequence[str], rows: Iterable[Sequence[str]], /) -> None:
        r"""Write tab-separated records to stdout, header line first.

        Parameters
        ----------
        header
            The column names, one per field of every record that follows. Written even when
            ``rows`` is empty, so a consumer can always read the shape.
        rows
            One sequence of already-stringified cells per record. Consumed once, so a
            generator is fine and is the point on a 432-page document.

        Notes
        -----
        Written through the plain writer, never a ``Console``: ``rich`` would wrap a long
        cell at 80 columns off a tty and would eat ``[draft]`` in a name as markup, and
        either one breaks a format whose contract is "one record per line, fields split on
        tab".

        A tab, newline or carriage return inside a cell is replaced by a single space. The
        escape is **lossy on purpose**: the whole value of this format is that ``cut -f2``
        works on it and that ``wc -l`` counts records, and a reversible ``\t`` escape would
        push un-escaping onto every consumer to preserve bytes that ``--json`` already
        preserves properly. Reach for ``--json`` when the exact bytes matter; reach for this
        when the point is that ``rmspec ocr`` on 432 pages is a few hundred kilobytes of
        ``page_index<TAB>text`` instead of megabytes of JSON.
        """
        records = [header, *rows]
        self._consoles.plain.write("".join(f"{_dense_record(record)}\n" for record in records))

    def line(self, text: str, /) -> None:
        """Write one line to stdout with no formatting of any kind.

        Parameters
        ----------
        text
            The line, without its newline. Written verbatim: no wrapping, no markup
            parsing, no styling, which is what makes ``eval "$(rmspec env)"`` work.
        """
        self._consoles.plain.write(f"{text}\n")

    def display(self, renderable: RenderableType, /) -> None:
        """Show something to a human on stderr.

        Parameters
        ----------
        renderable
            A string, table, or any other ``rich`` renderable.
        """
        self._consoles.human.print(renderable)

    def warn(self, text: str, /) -> None:
        """Warn a human on stderr, never on stdout.

        Parameters
        ----------
        text
            The warning. This is the stream the legacy tree had nothing on, which is why
            its progress lines landed in the middle of its JSON.
        """
        self._consoles.human.print(f"warning: {text}", style="yellow")

    def report_degradations(self, degradations: Sequence[Degradation], /) -> None:
        """Summarise the substitutions a run made instead of failing.

        Parameters
        ----------
        degradations
            The tuple a use-case result carries. An empty sequence prints nothing, so a
            caller does not have to check first.
        """
        for degradation in degradations:
            self.warn(f"{degradation.kind.value}: {degradation.subject} -- {degradation.detail}")

    def fail(self, err: RmspecError, /, *, next_action: NextAction | None = None) -> int:
        """Render a failure on both streams as each stream's rules require, and score it.

        Parameters
        ----------
        err
            The failure that ended the run.
        next_action
            The literal command that would fix it, when one exists.

        Returns
        -------
        int
            The status from :func:`~rmspec.domain.errors.exit_code`, for the command to
            return. Chosen by the domain, never here.

        Notes
        -----
        The envelope goes to stdout only in :attr:`OutputMode.JSON`; the human sentence goes
        to stderr in every mode. A machine caller therefore gets a parseable failure *and* a
        log line, and neither stream ever contains the other's content.

        :attr:`OutputMode.DENSE` deliberately writes **nothing** to stdout on failure. Its
        stdout is a homogeneous record stream, and appending a JSON object to a half-written
        one would break the consumer that was reading it with ``cut``. A dense run's failure
        contract is the exit status plus the stderr sentence -- and a caller that wants the
        structured failure asks for ``--json``, which is what that mode is for.
        """
        envelope = ErrorEnvelope.of(err)
        if self._mode is OutputMode.JSON:
            document: dict[str, Any] = {
                API_VERSION_KEY: API_VERSION,
                TYPE_KEY: ERROR_RESPONSE_TYPE,
                ERROR_KEY: envelope.as_document(),
            }
            if next_action is not None:
                document[NEXT_ACTION_KEY] = next_action.model_dump(mode="json")
            self._consoles.data.print_json(data=document, highlight=False)
        self._consoles.human.print(f"error: {err.message}", style="bold red")
        if err.remediation is not None:
            self._consoles.human.print(f"try: {err.remediation}", style="yellow")
        for candidate in envelope.candidates or ():
            self._consoles.human.print(f"  {candidate.uuid}  {candidate.name}")
        return envelope.exit_code
