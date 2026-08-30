"""``rmspec device info``: what the attached tablet is, and every fact it cannot answer.

Replaces legacy ``device info``, which printed five fields and gave two of them the name of a
different fact. Correcting those two names is the whole reason this command is in the rename
list, so both corrections are stated here rather than left implicit in a format string.

**A build stamp is not a firmware version.** Legacy printed the tablet's build stamp -- a
fourteen-digit datestamp -- under the label "Firmware". That is the number a user quotes back
when asking whether a feature exists, and it orders against no released version, so the wrong
label on it is worse than no answer at all. The port field is ``firmware`` and its contract is
a version, so this command labels it **"firmware version"** and prints whatever the port
answered. Which file an adapter opens to answer it is an adapter fact: a table here saying
otherwise would be the second source of truth :mod:`rmspec.app.facts` refuses to keep, and it
would go on asserting itself after the adapter changed.

**A SoC uid is not the serial a user reads.** Legacy printed the system-on-chip unique id under
the label "Serial". They are two different facts, and the one a user sees in the tablet's own
Settings is readable by no transport this project has -- it exists only inside a file this
project may never open. So this command prints no SoC uid under any label, labels the port's
``serial`` field **"device serial (the one the tablet's Settings shows)"**, and reports its
absence through whichever unavailability sentence the port earned rather than substituting a
different fact under its name.

Three absences stay three, and the third has three sentences
-----------------------------------------------------------
:class:`~rmspec.app.ReportDeviceFactsResult` partitions both port models into five places, and
this command keeps all five apart. ``unsupported``, ``unanswered`` and ``not_requested`` are
three different claims -- cannot ask, asked and got nothing usable, did not ask -- and
collapsing them into one "missing" list is what makes a report useless: a user cannot tell
which of the three they could do something about. Each gets its own rows and its own sentence.

Inside ``unsupported``, :attr:`~rmspec.domain.ports.device.UnsupportedField.supported_by`
selects one of three sentences:

* a non-empty tuple -- *this* transport cannot and those can, so the sentence names them and
  the user has something to change;
* ``()`` -- **no** transport can, so the sentence says that and suggests nothing, because
  "try SSH" here would be a lie;
* ``None`` -- the adapter named the field and claimed nothing further, so the sentence says the
  alternative was not stated rather than inventing one.

The third is the one worth naming. Adapters are only now learning to annotate, so many entries
still arrive as ``None``; printing "try ssh" for them would be advice this CLI cannot support
and would erase the difference between a claim and a silence. Reporting "no alternative was
stated" costs one clause and stays true whichever way an adapter later goes.

Labels are for people; the machine paths carry the port's own names
------------------------------------------------------------------
``HUMAN`` renders :data:`_LABELS`. ``JSON`` is ``model_dump`` and ``DENSE``'s ``field`` column
is the port's field name, unlabelled. A label is prose -- it is precisely the thing this
command changed -- and prose is not a key an agent should be matching on. The same reasoning
keeps the scaled byte figure out of both machine paths: it is a rounding a consumer cannot
undo, and the exact count is already there.

No dependency probe
-------------------
:meth:`~rmspec.cli._invoke.Invoked.probe` is not called. Every feature it can prove is an
optional module for rendering, decoding or a model call, and this command does none of those;
which transport it reaches the tablet over is ``RMSPEC_TRANSPORT``'s decision and the
container's to act on. Probing ``device-ssh`` unconditionally would refuse a working USB run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from pydantic import BaseModel
from rich.table import Table

from rmspec.app import ReportDeviceFacts, ReportDeviceFactsRequest
from rmspec.cli._invoke import DenseFlag, Invoked, JsonFlag, run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import NextAction, OutputMode

if TYPE_CHECKING:
    from rmspec.app import ReportDeviceFactsResult
    from rmspec.domain.ports.device import UnsupportedField

__all__ = ["ResourcesFlag", "info"]

_INVOCATION: Final = "device info"
"""The invocation a user types, which is also this command's key in :data:`RESPONSE_TYPES`.

Keyed by the space-joined words rather than by the leaf name, because ``device`` is a group and
``info`` alone would be a different command. Spelled once so the discriminator and the manifest
entry cannot drift apart.
"""

ResourcesFlag = Annotated[bool, Parameter(name="--resources", negative="--no-resources")]
"""``--resources`` / ``--no-resources``: whether to read the volatile gauges as well.

The one boolean in this CLI whose negative form is wanted, and it is spelled out rather than
left to cyclopts to generate. Everywhere else a flag carries ``negative=""`` so that
``--help`` and ``rmspec manifest`` cannot disagree with what the parser accepts; here the
negative is a real request -- ``ReportDeviceFactsRequest.include_resources`` defaults to
``True`` and costs a second round trip -- so it is declared, documented and listed like any
other flag.

Passing ``--no-resources`` does not hide the gauges. Every gauge name moves into the result's
``not_requested`` tuple and gets a row saying so, which is what keeps a cheaper invocation from
reading like a silent device.
"""

_STATE_FACT: Final = "fact"
"""The field is a fixed fact the device answered. ``value`` is what it answered."""

_STATE_GAUGE: Final = "gauge"
"""The field is a memory or storage reading the device answered, in bytes."""

_STATE_UNSUPPORTED_ELSEWHERE: Final = "unsupported_elsewhere"
"""This transport cannot answer the field and the port named ones that can.

``value`` is those transports, comma-separated, in the order the port gave them.
"""

_STATE_UNSUPPORTED_NOWHERE: Final = "unsupported_nowhere"
"""No transport can answer the field. The device serial is this case."""

_STATE_UNSUPPORTED_UNSTATED: Final = "unsupported_unstated"
"""This transport cannot answer the field and nothing was claimed about any other.

A distinct state rather than a synonym for :data:`_STATE_UNSUPPORTED_NOWHERE`, because "nobody
can" and "nobody said" are different facts and only one of them tells a user to stop asking.
"""

_STATE_UNANSWERED: Final = "unanswered"
"""The device was asked for the field and did not answer it usably. Asking again may work."""

_STATE_NOT_REQUESTED: Final = "not_requested"
"""This run did not ask for the field, because ``--no-resources`` was passed."""

_STATES: Final = (
    _STATE_FACT,
    _STATE_GAUGE,
    _STATE_UNSUPPORTED_ELSEWHERE,
    _STATE_UNSUPPORTED_NOWHERE,
    _STATE_UNSUPPORTED_UNSTATED,
    _STATE_UNANSWERED,
    _STATE_NOT_REQUESTED,
)
"""Every value the ``DENSE`` ``state`` column may hold, as a closed set.

``grep unsupported_nowhere`` is the point: a caller that knows the closed set decides once how
to treat each state instead of matching on a sentence that may be reworded. The three
``unsupported_*`` members are one family and are mutually exclusive, so a reader counting
unavailable fields sums them rather than parsing a transport list to find out which it has.
"""

_DENSE_HEADER: Final = ("field", "state", "value")
"""The ``DENSE`` columns: the port's field name, its state, and the reading or the transports.

Three columns rather than every field of the result. ``field`` is the identity, ``state`` is
what this command is *for* -- it is the five-way partition, which is the answer -- and
``value`` carries the only thing left that is not derivable from the other two.
"""

_HUMAN_HEADER: Final = ("field", "report")
"""The ``HUMAN`` columns: the label from :data:`_LABELS`, and one sentence about the field."""

_LABELS: Final = {
    "firmware": "firmware version",
    "model": "hardware model",
    "serial": "device serial (the one the tablet's Settings shows)",
    "total_memory_bytes": "memory, total",
    "available_memory_bytes": "memory, available",
    "total_storage_bytes": "document storage, total",
    "available_storage_bytes": "document storage, available",
}
"""What a person is shown for each port field name, and the two corrections this command is for.

``firmware`` says **version** because the value legacy printed under "Firmware" was a build
stamp, and a build stamp is not a version. ``serial`` names **the one the tablet's Settings
shows** because the value legacy printed under "Serial" was the SoC uid, a different fact --
and naming which serial is meant is what stops a reader assuming the unavailability sentence is
about some other identifier. Neither legacy wording is reprinted.

A test pins these keys against both port models' fields, so a field added to
:class:`~rmspec.domain.ports.device.DeviceFacts` or
:class:`~rmspec.domain.ports.device.DeviceResources` fails the build here instead of appearing
under its bare field name. :func:`_label` still falls back to that name, because a report that
lists an unlabelled field is better than one that drops it.
"""

_SENTENCE_NOWHERE: Final = "not available over any transport"
"""What ``()`` earns: no transport can, so there is nothing to suggest."""

_SENTENCE_UNSTATED: Final = "not available over this transport; no alternative was stated"
"""What ``None`` earns: the field was named and nothing further was claimed about it."""

_SENTENCE_UNANSWERED: Final = "the device was asked and did not answer usably"
"""What an unanswered field earns. Not a degradation and not a failure -- one unparseable
reading must not fail the whole report, which is why the port returns it as an absence."""

_SENTENCE_NOT_REQUESTED: Final = "not requested; pass --resources to read it"
"""What ``--no-resources`` earns, naming the flag that would fill the row in."""

_BYTE_SCALES: Final = ((1024**3, "GiB"), (1024**2, "MiB"), (1024, "KiB"))
"""Binary scales for a gauge, largest first, so the first that fits is the one used."""


def _scaled_bytes(value: int, /) -> str:
    """Render one gauge reading for a person without losing the exact count.

    Parameters
    ----------
    value
        The reading in bytes, as the port reported it.

    Returns
    -------
    str
        A scaled figure and the exact count, as in ``3.5 GiB (3758096384 bytes)``. Below a
        kibibyte the scaled figure would say nothing the count does not, so only the count is
        printed.

    Notes
    -----
    :class:`~rmspec.app.ReportedGauge` keeps its value an ``int`` because "a caller formats
    bytes, and stringifying here would oblige it to parse them back". This is that caller, and
    this is the only place in this module that formats a number: ``JSON`` and ``DENSE`` both
    carry the count itself, since a scaled figure is a rounding a consumer cannot undo.
    """
    for scale, unit in _BYTE_SCALES:
        if value >= scale:
            return f"{value / scale:.1f} {unit} ({value} bytes)"
    return f"{value} bytes"


def _label(name: str, /) -> str:
    """Give the display label for one port field name.

    Parameters
    ----------
    name
        The port's own field name, such as ``available_storage_bytes``.

    Returns
    -------
    str
        The label from :data:`_LABELS`, or *name* itself when the table does not hold it --
        which a test makes impossible for the fields that exist today, and which keeps a field
        added tomorrow in the report rather than out of it.
    """
    return _LABELS.get(name, name)


class _Reported(BaseModel, frozen=True, extra="forbid"):
    """One line of the report: which field, what became of it, and how to say so.

    Built once per field and projected twice -- three columns for ``DENSE``, a label and a
    sentence for ``HUMAN`` -- so the two renderings cannot disagree about which of the five
    places a field ended up in, and the five-way partition is walked exactly once.
    """

    field: str
    """The port's own field name, such as ``serial``. Never a label."""

    state: str
    """Which member of :data:`_STATES` this field is in."""

    value: str
    """The reading for an answered field, the answering transports for an unsupported field
    that named some, and empty for every other state -- because in those states there is
    nothing to report but the state itself."""

    sentence: str
    """What ``HUMAN`` prints in this field's row: the reading, or why there is none."""


def _unsupported_row(entry: UnsupportedField, /) -> _Reported:
    """Turn one unavailable field into the row its ``supported_by`` earns.

    Parameters
    ----------
    entry
        The port's declaration: a field name, and which transports can answer it, or ``()``
        for none, or ``None`` for no claim either way.

    Returns
    -------
    _Reported
        A row in exactly one of the three ``unsupported_*`` states. ``None`` is checked before
        emptiness, because ``not None`` is also true and collapsing the two would turn every
        unannotated field into a claim that nothing can answer it.
    """
    if entry.supported_by is None:
        return _Reported(
            field=entry.name,
            state=_STATE_UNSUPPORTED_UNSTATED,
            value="",
            sentence=_SENTENCE_UNSTATED,
        )
    if not entry.supported_by:
        return _Reported(
            field=entry.name,
            state=_STATE_UNSUPPORTED_NOWHERE,
            value="",
            sentence=_SENTENCE_NOWHERE,
        )
    kinds = tuple(kind.value for kind in entry.supported_by)
    return _Reported(
        field=entry.name,
        state=_STATE_UNSUPPORTED_ELSEWHERE,
        value=",".join(kinds),
        sentence=f"not available over this transport; try {', '.join(kinds)}",
    )


def _rows(result: ReportDeviceFactsResult, /) -> tuple[_Reported, ...]:
    """Flatten the result's five places into one ordered list of rows.

    Parameters
    ----------
    result
        The report, whose five tuples partition both port models' fields.

    Returns
    -------
    tuple[_Reported, ...]
        Answered facts, answered gauges, then the three absences in the order the result
        declares them. Order is stable across runs because every tuple in the result is
        already in the port's declaration order, so two runs of the same command against the
        same tablet print the same rows in the same places.
    """
    return (
        *(
            _Reported(field=fact.name, state=_STATE_FACT, value=fact.value, sentence=fact.value)
            for fact in result.facts
        ),
        *(
            _Reported(
                field=gauge.name,
                state=_STATE_GAUGE,
                value=str(gauge.value),
                sentence=_scaled_bytes(gauge.value),
            )
            for gauge in result.gauges
        ),
        *(_unsupported_row(entry) for entry in result.unsupported),
        *(
            _Reported(
                field=name,
                state=_STATE_UNANSWERED,
                value="",
                sentence=_SENTENCE_UNANSWERED,
            )
            for name in result.unanswered
        ),
        *(
            _Reported(
                field=name,
                state=_STATE_NOT_REQUESTED,
                value="",
                sentence=_SENTENCE_NOT_REQUESTED,
            )
            for name in result.not_requested
        ),
    )


def _table(rows: tuple[_Reported, ...], /) -> Table:
    """Build the table a person reads on stderr.

    Parameters
    ----------
    rows
        Every row of the report, in :func:`_rows` order.

    Returns
    -------
    ~rich.table.Table
        Two columns: the label, and one sentence. Every row reads as a statement, so an
        unavailable field is a sentence rather than the empty cell legacy left behind.
    """
    table = Table(*_HUMAN_HEADER, title="device")
    for row in rows:
        table.add_row(_label(row.field), row.sentence)
    return table


def _next_action(result: ReportDeviceFactsResult, /) -> NextAction | None:
    """Give the obvious next command, when this run left one.

    Parameters
    ----------
    result
        The report.

    Returns
    -------
    ~rmspec.cli._output.NextAction | None
        The full invocation that would read the gauges this run skipped, or ``None`` when it
        did not skip any. ``not_requested`` is non-empty for exactly one reason --
        ``--no-resources`` -- so the hint is derived from the result rather than from the flag,
        and it cannot be printed for a run that did ask.
    """
    if not result.not_requested:
        return None
    return NextAction(
        command=f"rmspec {_INVOCATION} --resources",
        purpose="read the memory and storage gauges this run did not ask for",
    )


def info(
    *,
    resources: ResourcesFlag = True,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Report what the attached tablet is, and name every fact it cannot answer.

    Parameters
    ----------
    resources
        Read the memory and storage gauges as well as the fixed facts, at the cost of a second
        round trip. ``--no-resources`` reports the fixed facts alone and says of every gauge
        that this run did not ask for it, rather than leaving it looking unanswered.
    json
        Emit the one envelope on stdout instead of a table on stderr. ``data`` is the whole
        ``ReportDeviceFactsResult``, so the three unavailability cases arrive as
        ``supported_by`` being a non-empty list, an empty list, or ``null``.
    dense
        Emit tab-separated ``field``, ``state``, ``value`` records on stdout, one per field of
        both port models. ``state`` is a closed vocabulary, so ``unsupported_nowhere`` is
        greppable and no sentence has to be parsed.

    Returns
    -------
    int
        ``0``. The device errors the port raises are the single boundary's to render.

    Notes
    -----
    Two labels legacy got wrong are the reason this command exists; see this module's
    docstring for both, and for why ``None`` in ``supported_by`` is reported as an unstated
    alternative rather than guessed at.
    """

    def body(invoked: Invoked) -> int:
        result = invoked.get(ReportDeviceFacts).report(
            ReportDeviceFactsRequest(include_resources=resources)
        )
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=RESPONSE_TYPES[_INVOCATION],
                degradations=result.degradations,
                next_action=_next_action(result),
            )
            return 0
        rows = _rows(result)
        invoked.report(result.degradations)
        if invoked.out.mode is OutputMode.DENSE:
            invoked.out.rows(_DENSE_HEADER, ((row.field, row.state, row.value) for row in rows))
        else:
            invoked.out.display(_table(rows))
        return 0

    return run(body, json=json, dense=dense)
