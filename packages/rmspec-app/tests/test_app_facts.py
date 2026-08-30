"""The five-way partition, both meanings of ``None`` at the port, and the two mislabels.

How a ``DeviceFactsSource`` is bound here, and why
--------------------------------------------------
With a local in-memory fake annotated against the Protocol, for the reasons
``test_app_resolve.py`` states at length and does not need repeating: ``rmspec.app`` may
import ``rmspec.domain`` and nothing else, the architecture check only scans ``src/`` so an
adapter import here would pass the gate while breaking what the gate protects, and
``rmspec.device.testing``'s shipped doubles run the parent package's ``__init__`` and would
make a pure-policy suite need ``paramiko`` and ``httpx`` installed. Conformance is checked
by the type gate rather than by convention: every construction below passes
``_InMemoryFactsSource`` to ``ReportDeviceFacts(facts_source=...)``, whose parameter is
annotated ``DeviceFactsSource``.

The fake implements both port methods because the Protocol has two, and carries two seams:
a whole-transport ``failure``, because a dead cable reported as a device that answered
nothing is the failure the port forbids, and one call counter per method, because declining
the gauges is only worth a flag if it actually costs one round trip instead of two.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rmspec.app.facts import (
    _FACT_NAMES,
    _GAUGE_NAMES,
    ReportDeviceFacts,
    ReportDeviceFactsRequest,
    ReportDeviceFactsResult,
    ReportedFact,
    ReportedGauge,
)
from rmspec.domain.errors import DeviceUnreachable, RmspecError, TransportKind
from rmspec.domain.ports.device import (
    DeviceFacts,
    DeviceFactsSource,
    DeviceResources,
    UnsupportedField,
)

BUILD_STAMP = "20260612085811"
IMG_VERSION = "3.27.3.0"
SOC_UID = "13c6b8e4a9f2"

GIBIBYTE = 1024**3


class _InMemoryFactsSource:
    """A :class:`DeviceFactsSource` over one pair of readings, with a killable transport."""

    def __init__(
        self,
        facts: DeviceFacts,
        resources: DeviceResources | None = None,
        failure: RmspecError | None = None,
    ) -> None:
        self.fact_calls = 0
        self.resource_calls = 0
        self._facts = facts
        self._resources = DeviceResources() if resources is None else resources
        self._failure = failure

    def read_facts(self) -> DeviceFacts:
        """Return the fixed facts, or die the way a transport does."""
        self.fact_calls += 1
        if self._failure is not None:
            raise self._failure
        return self._facts

    def read_resources(self) -> DeviceResources:
        """Return one gauge reading, or die the way a transport does."""
        self.resource_calls += 1
        if self._failure is not None:
            raise self._failure
        return self._resources


def _report(
    facts: DeviceFacts,
    resources: DeviceResources | None = None,
    *,
    include_resources: bool = True,
) -> ReportDeviceFactsResult:
    source = _InMemoryFactsSource(facts, resources)
    return ReportDeviceFacts(facts_source=source).report(
        ReportDeviceFactsRequest(include_resources=include_resources)
    )


def _named(
    reported: tuple[ReportedFact, ...] | tuple[ReportedGauge, ...] | tuple[UnsupportedField, ...],
) -> list[str]:
    return [item.name for item in reported]


def _claim(result: ReportDeviceFactsResult, name: str) -> tuple[TransportKind, ...] | None:
    """Return what the report says about which transports can answer one absent name."""
    (entry,) = [item for item in result.unsupported if item.name == name]
    return entry.supported_by


def _assign(target: object, field: str, value: object) -> None:
    """Assign through ``setattr`` so frozen-ness is a runtime fact, not a type error.

    Same reason ``test_app_resolve.py`` gives: a direct attribute assignment is rejected by
    the type gate before pydantic can be shown rejecting it, and this repository allows no
    ``type: ignore`` to get past that.
    """
    setattr(target, field, value)


# ─────────────────────── the facts a transport did answer ───────────────────────


def test_an_answered_fact_is_reported_under_the_ports_own_name():
    """``firmware``, not "Firmware": a display label is the CLI's to choose."""
    result = _report(DeviceFacts(firmware=IMG_VERSION, model="reMarkable Ferrari"))
    assert ReportedFact(name="firmware", value=IMG_VERSION) in result.facts


def test_the_facts_are_reported_in_the_ports_declaration_order():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, model="reMarkable Ferrari", serial="RM123"),
        include_resources=False,
    )
    assert _named(result.facts) == ["firmware", "model", "serial"]


def test_the_version_a_user_recognises_is_whatever_the_port_reported():
    """Legacy printed the build stamp under "Firmware"; this layer relabels nothing."""
    result = _report(DeviceFacts(firmware=IMG_VERSION))
    (fact,) = result.facts
    assert fact.value == IMG_VERSION
    assert fact.value != BUILD_STAMP


# ──────────────────── unsupported: not available over this transport ────────────────────


def test_a_field_the_transport_cannot_ask_is_named_rather_than_valued():
    result = _report(DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"model"})))
    assert _named(result.unsupported) == ["model"]
    assert "model" not in _named(result.facts)
    # A bare name claims nothing about other transports, which is all an adapter that passes
    # a name set ever knew.
    assert _claim(result, "model") is None


def test_the_serial_is_unsupported_on_both_transports_and_is_never_the_soc_uid():
    """There is no readable source for the device serial, so no fact may wear its name."""
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"serial"})),
        DeviceResources(available_storage_bytes=GIBIBYTE),
    )
    assert "serial" in _named(result.unsupported)
    assert "serial" not in _named(result.facts)
    assert SOC_UID not in [fact.value for fact in result.facts]


def test_an_unsupported_field_reaches_the_caller_as_a_name_it_must_render():
    """The port's own docstring dictates the sentence: "not available over this transport"."""
    result = _report(
        DeviceFacts(unsupported=frozenset({"firmware", "model", "serial"})),
        include_resources=False,
    )
    assert sorted(_named(result.unsupported)) == ["firmware", "model", "serial"]
    assert result.facts == ()


def test_a_field_no_transport_can_answer_is_told_apart_from_one_this_transport_cannot():
    """The distinction a bare name set could not express, and both halves are real."""
    result = _report(
        DeviceFacts(
            unsupported=frozenset(
                {
                    UnsupportedField(name="serial", supported_by=()),
                    UnsupportedField(name="firmware", supported_by=(TransportKind.SSH,)),
                    "model",
                }
            ),
        ),
        include_resources=False,
    )
    # Empty is a real answer: stop asking, no transport reads this.
    assert _claim(result, "serial") == ()
    # Non-empty: change transports.
    assert _claim(result, "firmware") == (TransportKind.SSH,)
    # Bare: this transport cannot, and nothing is claimed about the others.
    assert _claim(result, "model") is None
    assert sorted(_named(result.unsupported)) == ["firmware", "model", "serial"]


def test_an_unsupported_gauge_may_name_the_transport_that_can_read_it():
    """The gauges are as asymmetric as the facts, which is why both models carry the shape."""
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"model", "serial"})),
        DeviceResources(
            unsupported=frozenset(
                {
                    UnsupportedField(
                        name="total_memory_bytes",
                        supported_by=(TransportKind.SSH,),
                    ),
                    "available_memory_bytes",
                    "total_storage_bytes",
                    "available_storage_bytes",
                }
            ),
        ),
    )
    assert _claim(result, "total_memory_bytes") == (TransportKind.SSH,)
    assert _claim(result, "available_memory_bytes") is None


def test_the_transport_claim_rides_on_the_unsupported_entry_and_adds_no_fourth_tuple():
    """Who could answer this is a detail of one absence, not a fourth kind of absence."""
    absence_tuples = {
        name
        for name, field in ReportDeviceFactsResult.model_fields.items()
        if name not in {"facts", "gauges", "degradations"}
    }
    assert absence_tuples == {"unsupported", "unanswered", "not_requested"}
    # And only the one that can name an alternative carries entries; the other two are names.
    assert ReportDeviceFactsResult.model_fields["unanswered"].annotation == tuple[str, ...]
    assert ReportDeviceFactsResult.model_fields["not_requested"].annotation == tuple[str, ...]


# ──────────────────── unanswered: asked for, and no usable answer ────────────────────


def test_a_field_asked_for_and_unanswered_is_a_different_fact_from_an_unsupported_one():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"serial"})),
        include_resources=False,
    )
    assert _named(result.unsupported) == ["serial"]
    assert result.unanswered == ("model",)


def test_every_absent_field_is_named_exactly_once():
    result = _report(DeviceFacts(), include_resources=False)
    assert result.unanswered == ("firmware", "model", "serial")
    assert result.unsupported == ()
    assert result.facts == ()


# ─────────────────────────────── the volatile gauges ───────────────────────────────


def test_an_answered_gauge_is_reported_in_bytes_under_the_ports_name():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION),
        DeviceResources(total_storage_bytes=8 * GIBIBYTE, available_storage_bytes=GIBIBYTE),
    )
    assert ReportedGauge(name="available_storage_bytes", value=GIBIBYTE) in result.gauges
    assert _named(result.gauges) == ["total_storage_bytes", "available_storage_bytes"]


def test_a_gauge_reading_of_zero_is_answered_rather_than_absent():
    """A full partition read as falsy would report free space as "the device said nothing"."""
    result = _report(
        DeviceFacts(firmware=IMG_VERSION),
        DeviceResources(total_storage_bytes=8 * GIBIBYTE, available_storage_bytes=0),
    )
    assert ReportedGauge(name="available_storage_bytes", value=0) in result.gauges
    assert "available_storage_bytes" not in result.unanswered


def test_an_unsupported_gauge_is_named_after_the_unsupported_facts():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"serial"})),
        DeviceResources(
            total_storage_bytes=8 * GIBIBYTE,
            available_storage_bytes=GIBIBYTE,
            unsupported=frozenset({"total_memory_bytes", "available_memory_bytes"}),
        ),
    )
    assert _named(result.unsupported) == [
        "serial",
        "total_memory_bytes",
        "available_memory_bytes",
    ]


def test_an_unanswered_gauge_joins_the_unanswered_facts_in_declaration_order():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"serial"})),
        DeviceResources(total_memory_bytes=4 * GIBIBYTE),
    )
    assert result.unanswered == (
        "model",
        "available_memory_bytes",
        "total_storage_bytes",
        "available_storage_bytes",
    )


# ──────────────── the partition is total, which is what removes the None ────────────────


def test_the_five_places_partition_every_field_of_both_port_models():
    result = _report(
        DeviceFacts(firmware=IMG_VERSION, unsupported=frozenset({"serial"})),
        DeviceResources(
            total_memory_bytes=4 * GIBIBYTE,
            available_memory_bytes=GIBIBYTE,
            unsupported=frozenset({"total_storage_bytes"}),
        ),
    )
    placed = [
        *_named(result.facts),
        *_named(result.gauges),
        *_named(result.unsupported),
        *result.unanswered,
        *result.not_requested,
    ]
    assert sorted(placed) == sorted([*_FACT_NAMES, *_GAUGE_NAMES])
    assert len(placed) == len(set(placed))


def test_a_reported_value_is_never_optional():
    """Which is what stops a caller rendering "unavailable" as an empty cell."""
    assert ReportedFact.model_fields["value"].annotation is str
    assert ReportedGauge.model_fields["value"].annotation is int


def test_no_reported_value_is_none_even_when_most_fields_are_absent():
    result = _report(DeviceFacts(model="reMarkable Ferrari"))
    assert all(fact.value is not None for fact in result.facts)
    assert all(gauge.value is not None for gauge in result.gauges)


# ──────────────────── declining the gauges, and what that costs ────────────────────


def test_declining_the_gauges_costs_one_round_trip_rather_than_two():
    source = _InMemoryFactsSource(DeviceFacts(firmware=IMG_VERSION))
    ReportDeviceFacts(facts_source=source).report(
        ReportDeviceFactsRequest(include_resources=False)
    )
    assert source.fact_calls == 1
    assert source.resource_calls == 0


def test_declining_the_gauges_names_them_rather_than_omitting_them():
    """Otherwise "nobody asked" is indistinguishable from "the device said nothing"."""
    result = _report(DeviceFacts(firmware=IMG_VERSION), include_resources=False)
    assert result.not_requested == _GAUGE_NAMES
    assert result.gauges == ()
    assert set(result.unanswered).isdisjoint(_GAUGE_NAMES)


def test_asking_for_the_gauges_leaves_nothing_unrequested():
    result = _report(DeviceFacts(firmware=IMG_VERSION))
    assert result.not_requested == ()


def test_the_gauges_are_included_by_default():
    source = _InMemoryFactsSource(DeviceFacts(firmware=IMG_VERSION))
    ReportDeviceFacts(facts_source=source).report(ReportDeviceFactsRequest())
    assert source.resource_calls == 1


def test_one_full_report_reads_each_port_method_once():
    source = _InMemoryFactsSource(
        DeviceFacts(firmware=IMG_VERSION),
        DeviceResources(total_memory_bytes=4 * GIBIBYTE),
    )
    ReportDeviceFacts(facts_source=source).report(ReportDeviceFactsRequest())
    assert (source.fact_calls, source.resource_calls) == (1, 1)


# ─────────────────── the names this module holds, against the port ───────────────────


def test_the_fact_names_are_exactly_the_ports_fields():
    """A field added to ``DeviceFacts`` must fail here rather than go unreported."""
    assert set(_FACT_NAMES) == set(DeviceFacts.model_fields) - {"unsupported"}


def test_the_gauge_names_are_exactly_the_ports_fields():
    assert set(_GAUGE_NAMES) == set(DeviceResources.model_fields) - {"unsupported"}


def test_the_two_name_sets_stay_disjoint():
    """The result reports one tuple per reason, not one per port model, so they must be."""
    assert set(_FACT_NAMES).isdisjoint(_GAUGE_NAMES)


def test_the_names_are_in_the_ports_declaration_order():
    assert list(_FACT_NAMES) == [
        name for name in DeviceFacts.model_fields if name != "unsupported"
    ]
    assert list(_GAUGE_NAMES) == [
        name for name in DeviceResources.model_fields if name != "unsupported"
    ]


# ───────────────────────────── the boundary conditions ─────────────────────────────


def test_nothing_here_is_ever_reported_as_a_substitution():
    """``DegradationKind`` has no member for "the device did not answer a field"."""
    result = _report(DeviceFacts(), include_resources=False)
    assert result.degradations == ()


def test_a_dead_transport_is_never_reported_as_a_device_that_answered_nothing():
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connection refused",
    )
    source = _InMemoryFactsSource(DeviceFacts(firmware=IMG_VERSION), failure=failure)
    with pytest.raises(DeviceUnreachable):
        ReportDeviceFacts(facts_source=source).report(ReportDeviceFactsRequest())


def test_the_fake_is_the_port_the_use_case_declares():
    source: DeviceFactsSource = _InMemoryFactsSource(DeviceFacts(firmware=IMG_VERSION))
    assert source.read_facts().firmware == IMG_VERSION
    assert source.read_resources().total_memory_bytes is None


def test_a_result_is_frozen():
    result = _report(DeviceFacts(firmware=IMG_VERSION))
    with pytest.raises(ValidationError, match="frozen"):
        _assign(result, "unsupported", ("serial",))


def test_a_result_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ReportDeviceFactsResult.model_validate(
            {
                "facts": (),
                "gauges": (),
                "unsupported": (),
                "unanswered": (),
                "not_requested": (),
                "degradations": (),
                "transport": "usb_web_api",
            }
        )


def test_a_request_is_frozen():
    request = ReportDeviceFactsRequest()
    declined = False
    with pytest.raises(ValidationError, match="frozen"):
        _assign(request, "include_resources", declined)
