"""Report the fixed facts of an attached tablet, and name the ones that cannot be had.

Replaces legacy ``device info``, which printed five fields and mislabelled two of them.
Both mislabels are the same mistake -- a readable file wearing the name of the fact the
user wanted -- and both are recorded in ``.claude/INVENTORY-legacy-cli.md`` §6 item 17.

**"Firmware" was ``/etc/version``**, which holds the build stamp ``20260612085811``. The
version a user recognises is ``IMG_VERSION`` in ``/etc/os-release``, measured ``3.27.3.0``
on the attached device. A build stamp under the label "Firmware" is worse than no answer:
it is the number a user quotes back when asking whether a feature exists, and it orders
against no released version.

**"Serial" was ``/sys/devices/soc0/serial_number``**, which is the SoC uid -- a different
fact wearing the right field's name. There is no readable source for the reMarkable device
serial at all: it exists only inside the tablet's secret-bearing configuration file, which
this project may never open and which ``tests/architecture/test_secret_containment.py``
forbids even naming here, because that file also holds a cleartext password and two JWTs. So
:attr:`~rmspec.domain.ports.device.DeviceFacts.serial` is named in that model's
``unsupported`` set by **both** transports, and this use case reports it as unavailable
rather than printing a different fact under its name.

Neither correction is applied here, and that is the point. This module reads whichever
field names the port declares and reports the values it is handed; which file an adapter
opens to answer ``firmware``, and whether a transport can answer ``serial`` at all, are
adapter facts. A table here saying "serial is unsupported over USB and SSH" would be a
second source of truth that keeps asserting it after an adapter learns to answer -- the
same argument :mod:`rmspec.app.capabilities` makes about the capability matrix.

Why nothing in this result is ``None``
--------------------------------------
:class:`~rmspec.domain.ports.device.DeviceFacts` gives ``None`` two distinct meanings, and
its own docstring insists a device-information command tell them apart: a field named in
``unsupported`` is one "this transport structurally cannot ask", displayed as "not
available over this transport", while a field that is ``None`` and *unnamed* was asked for
and not answered. A result that carried ``firmware=None`` would collapse both into the
value a caller renders as an empty cell.

So the result carries no optional value at all. Every field name the two port models
declare appears in exactly one of five places -- :attr:`ReportDeviceFactsResult.facts` and
:attr:`~ReportDeviceFactsResult.gauges` for the answered ones, which therefore always
carry a value, and :attr:`~ReportDeviceFactsResult.unsupported`,
:attr:`~ReportDeviceFactsResult.unanswered` and
:attr:`~ReportDeviceFactsResult.not_requested` as bare names for the three ways a field can
be absent. Membership *is* the state, so "unavailable" cannot be misread as "empty" by a
caller that only looks at values, and a JSON consumer sees the same partition a terminal
renderer does.

The fifth place is what makes :attr:`ReportDeviceFactsRequest.include_resources` honest. A
caller that asks for the fixed facts alone pays one round trip instead of two, which is the
inverse of the reason the port split ``read_resources`` out of ``read_facts`` in the first
place; but with only four places, the gauges of such a run would appear in none of them and
"nobody asked" would be indistinguishable from "the device said nothing".

Why an unanswered field is not a degradation
-------------------------------------------
:class:`~rmspec.domain.errors.DegradationKind` is closed and has no member meaning "the
device was asked for a fact and did not answer". Adding one is a reviewed change to the
domain, not something this module may decide -- the judgement
:mod:`rmspec.app.resolve` already made about excluding the trash. Nothing here substitutes
a value either: an absent field stays absent under its own name. So
:attr:`ReportDeviceFactsResult.degradations` is always empty, and it is still a required
field, because a caller that summarises degradations must not have to know which use cases
can produce them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, Field

from rmspec.domain.errors import Degradation

if TYPE_CHECKING:
    from rmspec.domain.ports.device import DeviceFacts, DeviceFactsSource, DeviceResources

__all__ = [
    "ReportDeviceFacts",
    "ReportDeviceFactsRequest",
    "ReportDeviceFactsResult",
    "ReportedFact",
    "ReportedGauge",
]

_FACT_NAMES: Final = ("firmware", "model", "serial")
"""The fields of :class:`~rmspec.domain.ports.device.DeviceFacts`, in declaration order.

Spelled out rather than read from ``model_fields`` so that every value below is reached by
attribute access the type checker can see. ``test_app_facts`` asserts this tuple is exactly
that model's fields minus ``unsupported``, so a field added to the port fails a test here
instead of silently going unreported.
"""

_GAUGE_NAMES: Final = (
    "total_memory_bytes",
    "available_memory_bytes",
    "total_storage_bytes",
    "available_storage_bytes",
)
"""The fields of :class:`~rmspec.domain.ports.device.DeviceResources`, in declaration order.

Held to the same test as :data:`_FACT_NAMES`, plus one more: the two tuples must stay
disjoint, because the result reports absent names in one tuple per reason rather than one
tuple per port model.
"""


class ReportedFact(BaseModel, frozen=True, extra="forbid"):
    """One fixed fact the device answered, under the port's own name for it.

    ``value`` is not optional. A fact the transport could not ask for, or asked for and did
    not get, is reported as a name in one of the result's absence tuples instead of as a
    fact with nothing in it -- which is what keeps "not available over this transport" from
    rendering as an empty cell.
    """

    name: str
    """The port's field name, such as ``firmware``. Never a display label."""

    value: str
    """What the device answered, exactly as the transport reported it."""


class ReportedGauge(BaseModel, frozen=True, extra="forbid"):
    """One memory or storage reading the device answered, in bytes.

    Separate from :class:`ReportedFact` because the value is a number: a caller formats
    bytes, and stringifying here would oblige it to parse them back.
    """

    name: str
    """The port's field name, such as ``available_storage_bytes``."""

    value: int = Field(ge=0)
    """The reading in bytes, at the instant the transport took it."""


class ReportDeviceFactsRequest(BaseModel, frozen=True, extra="forbid"):
    """Whether this report includes the volatile gauges as well as the fixed facts."""

    include_resources: bool = True
    """Read the memory and storage gauges too, at the cost of a second round trip.

    ``False`` reports the fixed facts alone and names every gauge in
    :attr:`ReportDeviceFactsResult.not_requested`, so a reader can tell a run that did not
    ask from a device that did not answer.
    """


class ReportDeviceFactsResult(BaseModel, frozen=True, extra="forbid"):
    """Every field of both port models, each in exactly one of five places.

    No field has a default, following the reason
    :class:`~rmspec.domain.ports.device.DeviceListing` gives for the same decision: a
    caller cannot construct this without stating what was unavailable, so the absence
    tuples cannot be forgotten at a construction site and default to "nothing missing".
    """

    facts: tuple[ReportedFact, ...]
    """The fixed facts the device answered, in the port's declaration order."""

    gauges: tuple[ReportedGauge, ...]
    """The memory and storage readings the device answered, in declaration order.

    One consistent reading: the port returns the totals alongside the free values from a
    single command, so a pair here never mixes two instants.
    """

    unsupported: tuple[str, ...]
    """Names this transport structurally cannot ask for, facts before gauges.

    Rendered as "not available over this transport". ``serial`` is here on every transport
    that exists today, and no adapter answers it with the SoC uid instead.
    """

    unanswered: tuple[str, ...]
    """Names the device was asked for and did not answer usably, facts before gauges.

    A different fact from :attr:`unsupported`: asking again over another transport may
    work. The port turns an unintelligible reading into this rather than raising, so one
    unparseable line never fails the whole report.
    """

    not_requested: tuple[str, ...]
    """Names this run did not ask for, because ``include_resources`` was ``False``.

    Empty otherwise. Exists so the five places are a total partition of both models'
    fields, and a reader cannot mistake a cheaper invocation for a silent device.
    """

    degradations: tuple[Degradation, ...]
    """Always empty here, and required anyway. See this module's docstring."""


def _fact_readings(facts: DeviceFacts) -> tuple[tuple[str, str | None], ...]:
    """Pair each fixed-fact field name with the value the transport reported.

    Parameters
    ----------
    facts
        One reading of the device's fixed facts.

    Returns
    -------
    tuple[tuple[str, str | None], ...]
        Name and value per field, in :data:`_FACT_NAMES` order.
    """
    values = (facts.firmware, facts.model, facts.serial)
    return tuple(zip(_FACT_NAMES, values, strict=True))


def _gauge_readings(resources: DeviceResources) -> tuple[tuple[str, int | None], ...]:
    """Pair each gauge field name with the reading the transport reported.

    Parameters
    ----------
    resources
        One reading of the device's memory and storage gauges.

    Returns
    -------
    tuple[tuple[str, int | None], ...]
        Name and value per field, in :data:`_GAUGE_NAMES` order.
    """
    values = (
        resources.total_memory_bytes,
        resources.available_memory_bytes,
        resources.total_storage_bytes,
        resources.available_storage_bytes,
    )
    return tuple(zip(_GAUGE_NAMES, values, strict=True))


def _partition[T](
    readings: tuple[tuple[str, T | None], ...],
    unsupported: frozenset[str],
    /,
) -> tuple[tuple[tuple[str, T], ...], tuple[str, ...], tuple[str, ...]]:
    """Split one port model's readings into answered, unsupported and unanswered.

    The ``unsupported`` set is consulted before the value, which is the order the port's
    invariant licenses: its validator rejects an ``unsupported`` set naming a field that
    carries a value, so a named field is always valueless and a valueless unnamed field was
    genuinely asked for and not answered.

    Parameters
    ----------
    readings
        Name and value per field, in the port's declaration order.
    unsupported
        The field names the transport declared it structurally cannot ask.

    Returns
    -------
    tuple[tuple[tuple[str, T], ...], tuple[str, ...], tuple[str, ...]]
        The answered name-value pairs, the unsupported names, and the unanswered names,
        each in declaration order.
    """
    answered: list[tuple[str, T]] = []
    absent: list[str] = []
    silent: list[str] = []
    for name, value in readings:
        if name in unsupported:
            absent.append(name)
        elif value is None:
            silent.append(name)
        else:
            answered.append((name, value))
    return tuple(answered), tuple(absent), tuple(silent)


class ReportDeviceFacts:
    """Report what the attached tablet is, in the port's vocabulary and nothing else.

    Pure policy over two records. It reads a
    :class:`~rmspec.domain.ports.device.DeviceFactsSource` at most twice per call and
    partitions what it got; it opens no file, runs no command, writes to no stream, and
    holds no state between calls -- so a caller that memoizes the fixed facts cannot be
    served a stale free-space number by this class.

    Notes
    -----
    The report is meant to be rendered by walking the four name tuples, so an unavailable
    field is a sentence rather than a blank::

        result = reporter.report(ReportDeviceFactsRequest())
        for fact in result.facts:
            print(f"{fact.name}: {fact.value}")
        for name in result.unsupported:
            print(f"{name}: not available over this transport")
    """

    def __init__(self, *, facts_source: DeviceFactsSource) -> None:
        self._facts_source = facts_source

    def report(self, request: ReportDeviceFactsRequest, /) -> ReportDeviceFactsResult:
        """Read the device's facts, and its gauges when they were asked for.

        Parameters
        ----------
        request
            Whether the volatile gauges are part of this report.

        Returns
        -------
        ReportDeviceFactsResult
            Every field of both port models, partitioned into answered, unsupported,
            unanswered and not-requested.

        Raises
        ------
        DeviceUnreachable
            Raised by the port and never degraded here: an unreachable tablet must not
            report as a device that answered nothing.
        DeviceAuthFailed
            Raised by the port.
        DeviceProtocolError
            Raised by the port.
        """
        facts = self._facts_source.read_facts()
        answered, unsupported, unanswered = _partition(_fact_readings(facts), facts.unsupported)
        gauges: tuple[ReportedGauge, ...] = ()
        not_requested: tuple[str, ...] = ()
        if request.include_resources:
            resources = self._facts_source.read_resources()
            readings, absent, silent = _partition(
                _gauge_readings(resources), resources.unsupported
            )
            gauges = tuple(ReportedGauge(name=name, value=value) for name, value in readings)
            unsupported += absent
            unanswered += silent
        else:
            not_requested = _GAUGE_NAMES
        return ReportDeviceFactsResult(
            facts=tuple(ReportedFact(name=name, value=value) for name, value in answered),
            gauges=gauges,
            unsupported=unsupported,
            unanswered=unanswered,
            not_requested=not_requested,
            degradations=(),
        )
