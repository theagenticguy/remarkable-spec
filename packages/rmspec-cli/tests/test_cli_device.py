"""``rmspec device info``: two corrected labels, and three ways one field can be unavailable.

Every test here binds a :class:`~rmspec.domain.ports.device.DeviceFactsSource` double over the
real transport binding, so nothing in this file opens an SSH session, an HTTP connection, or
touches an attached tablet. The three ``supported_by`` cases are exercised through those
doubles rather than through whichever case a shipped adapter happens to annotate today, because
the adapters are still learning to annotate and a test that depended on their current answer
would break for a reason that has nothing to do with this command.
"""

from __future__ import annotations

import inspect
import json as json_module
import os
from typing import TYPE_CHECKING, Any, Final

import pytest
from cyclopts import App
from dishka import Provider, Scope, provide

from rmspec.cli import _device
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.domain.errors import DeviceUnreachable, TransportKind, exit_code
from rmspec.domain.ports.device import (
    DeviceFacts,
    DeviceFactsSource,
    DeviceResources,
    UnsupportedField,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rmspec.cli._invoke import Invoked

_SETTINGS_PREFIX: Final = "RMSPEC_"

_GAUGE_NAMES: Final = (
    "total_memory_bytes",
    "available_memory_bytes",
    "total_storage_bytes",
    "available_storage_bytes",
)

_ANSWERED_FACTS: Final = DeviceFacts(
    firmware="3.27.3.0",
    model="reMarkable Paper Pro",
    unsupported=frozenset({UnsupportedField(name="serial", supported_by=())}),
)
"""Everything a transport can answer, plus the one fact no transport can.

``serial`` carries an empty ``supported_by``, which is the truth about it: the value a user
reads in the tablet's own Settings lives only inside a file this project may never open.
"""

_ANSWERED_RESOURCES: Final = DeviceResources(
    total_memory_bytes=8_589_934_592,
    available_memory_bytes=3_758_096_384,
    total_storage_bytes=5_242_880,
    available_storage_bytes=2048,
)
"""One reading per gauge, chosen so each lands on a different scale in ``HUMAN`` mode."""

_MIXED_FACTS: Final = DeviceFacts(
    unsupported=frozenset(
        {
            UnsupportedField(name="firmware", supported_by=(TransportKind.SSH,)),
            "model",
            UnsupportedField(name="serial", supported_by=()),
        }
    ),
)
"""All three ``supported_by`` cases at once, one per fact field.

``firmware`` names a transport that can answer it, ``serial`` says none can, and ``model`` is
declared as a bare name -- which is how an adapter that has not learned to annotate says it,
and what the app layer turns into ``supported_by=None``.
"""

_SILENT_RESOURCES: Final = DeviceResources(
    total_memory_bytes=8_589_934_592,
    available_memory_bytes=3_758_096_384,
)
"""Two gauges answered and two left ``None`` without being declared unsupported.

An undeclared ``None`` is the device having been asked and having said nothing usable, which is
a different absence from either unsupported case and gets its own row.
"""


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test here independent of the developer's own shell.

    ``load_settings`` reads the real environment and refuses an unknown ``RMSPEC_*``, so an
    exported variable would change what a test measures or fail it outright. ``COLUMNS`` pins
    the console width, because ``rich`` wraps at 80 columns off a tty and a wrapped table cell
    would make a sentence assertion fail for a reason that is not about the sentence.
    """
    # Set one first so the filter below always matches something: this file is measured for
    # branch coverage, and a filter that never fires is a half-taken branch on a machine with
    # nothing exported.
    monkeypatch.setenv(f"{_SETTINGS_PREFIX}TRANSPORT", "usb")
    for name in list(os.environ):
        if name.startswith(_SETTINGS_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")


class _FactsDouble:
    """A ``DeviceFactsSource`` that answers from two prepared readings, or refuses outright.

    Records which reads it served, so a test can prove ``--no-resources`` really costs one
    round trip instead of two rather than merely printing as though it did.
    """

    def __init__(
        self,
        *,
        facts: DeviceFacts,
        resources: DeviceResources,
        failure: DeviceUnreachable | None = None,
    ) -> None:
        self._facts = facts
        self._resources = resources
        self._failure = failure
        self.reads: list[str] = []

    def read_facts(self) -> DeviceFacts:
        if self._failure is not None:
            raise self._failure
        self.reads.append("facts")
        return self._facts

    def read_resources(self) -> DeviceResources:
        self.reads.append("resources")
        return self._resources


class _FactsProvider(Provider):
    """Bind one :class:`_FactsDouble` over the transport's real facts source.

    ``override=True`` on the one port the use case reaches through is what keeps these tests
    off the wire: nothing in the resulting graph opens a session or builds an HTTP client.
    """

    scope = Scope.REQUEST

    def __init__(self, source: DeviceFactsSource) -> None:
        super().__init__()
        self._source = source

    @provide(override=True)
    def facts_source(self) -> DeviceFactsSource:
        return self._source


@pytest.fixture
def bind(monkeypatch: pytest.MonkeyPatch) -> Callable[[DeviceFactsSource], None]:
    """Give a way to run the real command body against a double.

    ``providers=`` is test-only and the command never passes it, so the binding is injected by
    replacing the ``run`` the module calls. The command's own body still executes unchanged,
    which is the point: patching the use case instead would test the test.

    The real ``run`` is captured **once**, before any replacement, so binding a second double
    in one test replaces the first rather than chaining behind it -- two chained providers
    would both reach the container and the one that won would be an accident.
    """
    original = _device.run

    def _bind(source: DeviceFactsSource, /) -> None:
        provider = _FactsProvider(source)

        def patched(
            body: Callable[[Invoked], int],
            /,
            *,
            json: bool = False,
            dense: bool = False,
            providers: Sequence[Provider] = (),
        ) -> int:
            return original(body, json=json, dense=dense, providers=[*providers, provider])

        monkeypatch.setattr(_device, "run", patched)

    return _bind


def _document(captured: str, /) -> dict[str, Any]:
    """Parse the one envelope a ``--json`` run wrote on stdout."""
    document: dict[str, Any] = json_module.loads(captured)
    return document


def _data(captured: str, /) -> dict[str, Any]:
    """Parse a successful envelope and give the result it carries under ``data``."""
    payload: dict[str, Any] = _document(captured)["data"]
    return payload


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3_758_096_384, "3.5 GiB (3758096384 bytes)"),
        (5_242_880, "5.0 MiB (5242880 bytes)"),
        (2048, "2.0 KiB (2048 bytes)"),
        (512, "512 bytes"),
    ],
)
def test_a_gauge_is_scaled_to_the_largest_unit_that_fits(value: int, expected: str):
    assert _device._scaled_bytes(value) == expected


def test_every_port_field_has_a_label():
    fields = {name for name in DeviceFacts.model_fields if name != "unsupported"}
    fields |= {name for name in DeviceResources.model_fields if name != "unsupported"}
    assert set(_device._LABELS) == fields


def test_an_unlabelled_field_falls_back_to_its_port_name():
    assert _device._label("some_field_added_tomorrow") == "some_field_added_tomorrow"


def test_the_two_corrected_labels_do_not_reprint_the_legacy_wording():
    assert _device._LABELS["firmware"] == "firmware version"
    assert _device._LABELS["serial"] == "device serial (the one the tablet's Settings shows)"
    assert _device._LABELS["firmware"] != "Firmware"
    assert _device._LABELS["serial"] != "Serial"


def test_the_resources_flag_declares_both_forms_explicitly():
    app = App(name="info", default_command=_device.info)
    collection = app.assemble_argument_collection(parse_docstring=True)
    argument = next(item for item in collection if "--resources" in item.names)
    assert argument.negatives == ("--no-resources",)
    assert argument.is_flag()
    assert "--no-resources" in collection
    assert "--json" in collection
    assert "--dense" in collection
    assert inspect.signature(_device.info).parameters["resources"].default is True


def test_json_carries_the_whole_result_under_the_facts_discriminator(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_ANSWERED_FACTS, resources=_ANSWERED_RESOURCES))
    status = _device.info(json=True)
    document = _document(capsys.readouterr().out)
    assert status == 0
    assert document["api_version"] == "rmspec/v1"
    assert document["type"] == RESPONSE_TYPES["device info"] == "facts"
    assert document["degradations"] == []
    assert "next" not in document
    data: dict[str, Any] = document["data"]
    assert data["facts"] == [
        {"name": "firmware", "value": "3.27.3.0"},
        {"name": "model", "value": "reMarkable Paper Pro"},
    ]
    assert data["gauges"] == [
        {"name": "total_memory_bytes", "value": 8_589_934_592},
        {"name": "available_memory_bytes", "value": 3_758_096_384},
        {"name": "total_storage_bytes", "value": 5_242_880},
        {"name": "available_storage_bytes", "value": 2048},
    ]


def test_json_keeps_the_three_supported_by_cases_distinguishable(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_MIXED_FACTS, resources=_SILENT_RESOURCES))
    assert _device.info(json=True) == 0
    data = _data(capsys.readouterr().out)
    assert data["unsupported"] == [
        {"name": "firmware", "supported_by": ["ssh"]},
        {"name": "model", "supported_by": None},
        {"name": "serial", "supported_by": []},
    ]


def test_json_keeps_the_three_absences_in_three_places(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_MIXED_FACTS, resources=_SILENT_RESOURCES))
    assert _device.info(json=True) == 0
    data = _data(capsys.readouterr().out)
    assert [entry["name"] for entry in data["unsupported"]] == ["firmware", "model", "serial"]
    assert data["unanswered"] == ["total_storage_bytes", "available_storage_bytes"]
    assert data["not_requested"] == []


def test_dense_gives_each_unavailability_its_own_state(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_MIXED_FACTS, resources=_SILENT_RESOURCES))
    assert _device.info(dense=True) == 0
    captured = capsys.readouterr()
    records = [line.split("\t") for line in captured.out.splitlines()]
    assert records[0] == ["field", "state", "value"]
    states = {record[0]: record[1] for record in records[1:]}
    assert states["firmware"] == "unsupported_elsewhere"
    assert states["model"] == "unsupported_unstated"
    assert states["serial"] == "unsupported_nowhere"
    assert states["total_storage_bytes"] == "unanswered"
    assert states["available_memory_bytes"] == "gauge"
    values = {record[0]: record[2] for record in records[1:]}
    assert values["firmware"] == "ssh"
    assert values["model"] == ""
    assert values["serial"] == ""
    assert values["available_memory_bytes"] == "3758096384"
    assert captured.err == ""


def test_dense_names_every_transport_that_could_answer(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    facts = DeviceFacts(
        firmware="3.27.3.0",
        model="reMarkable Paper Pro",
        unsupported=frozenset(
            {
                UnsupportedField(
                    name="serial",
                    supported_by=(TransportKind.SSH, TransportKind.LOCAL_MIRROR),
                )
            }
        ),
    )
    bind(_FactsDouble(facts=facts, resources=_ANSWERED_RESOURCES))
    assert _device.info(dense=True) == 0
    records = [line.split("\t") for line in capsys.readouterr().out.splitlines()]
    assert ["serial", "unsupported_elsewhere", "ssh,local_mirror"] in records


def test_dense_reports_one_record_per_field_of_both_port_models(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_MIXED_FACTS, resources=_SILENT_RESOURCES))
    assert _device.info(dense=True) == 0
    records = [line.split("\t") for line in capsys.readouterr().out.splitlines()][1:]
    fields = {name for name in DeviceFacts.model_fields if name != "unsupported"}
    fields |= set(_GAUGE_NAMES)
    assert {record[0] for record in records} == fields
    assert len(records) == len(fields)
    assert {record[1] for record in records} <= set(_device._STATES)


def test_every_state_in_the_closed_set_is_reachable(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    seen: set[str] = set()
    for facts, resources, resources_flag in (
        (_MIXED_FACTS, _SILENT_RESOURCES, True),
        (_ANSWERED_FACTS, _ANSWERED_RESOURCES, False),
    ):
        bind(_FactsDouble(facts=facts, resources=resources))
        assert _device.info(dense=True, resources=resources_flag) == 0
        records = [line.split("\t") for line in capsys.readouterr().out.splitlines()][1:]
        seen |= {record[1] for record in records}
    assert seen == set(_device._STATES)
    assert len(_device._STATES) == len(set(_device._STATES))


def test_human_prints_a_different_sentence_for_each_supported_by_case(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_MIXED_FACTS, resources=_SILENT_RESOURCES))
    assert _device.info() == 0
    captured = capsys.readouterr()
    assert "not available over this transport; try ssh" in captured.err
    assert "not available over any transport" in captured.err
    assert "not available over this transport; no alternative was stated" in captured.err
    assert "the device was asked and did not answer usably" in captured.err
    assert captured.out == ""


def test_human_labels_the_two_facts_legacy_mislabelled(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_ANSWERED_FACTS, resources=_ANSWERED_RESOURCES))
    assert _device.info() == 0
    captured = capsys.readouterr()
    assert "firmware version" in captured.err
    assert "device serial (the one the tablet's Settings shows)" in captured.err
    assert "3.5 GiB (3758096384 bytes)" in captured.err
    assert captured.out == ""


def test_no_resources_names_every_gauge_and_offers_the_next_command(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    double = _FactsDouble(facts=_ANSWERED_FACTS, resources=_ANSWERED_RESOURCES)
    bind(double)
    assert _device.info(resources=False, json=True) == 0
    document = _document(capsys.readouterr().out)
    data: dict[str, Any] = document["data"]
    assert data["not_requested"] == list(_GAUGE_NAMES)
    assert data["gauges"] == []
    assert document["next"] == {
        "command": "rmspec device info --resources",
        "purpose": "read the memory and storage gauges this run did not ask for",
    }
    assert double.reads == ["facts"]


def test_no_resources_tells_a_human_which_flag_would_fill_the_rows_in(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    bind(_FactsDouble(facts=_ANSWERED_FACTS, resources=_ANSWERED_RESOURCES))
    assert _device.info(resources=False) == 0
    assert "not requested; pass --resources to read it" in capsys.readouterr().err


def test_resources_reads_the_gauges_and_returns_a_plain_int(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    double = _FactsDouble(facts=_ANSWERED_FACTS, resources=_ANSWERED_RESOURCES)
    bind(double)
    status = _device.info()
    capsys.readouterr()
    assert double.reads == ["facts", "resources"]
    assert status == 0
    assert not isinstance(status, bool)


def test_a_device_failure_renders_through_the_one_error_boundary(
    bind: Callable[[DeviceFactsSource], None],
    capsys: pytest.CaptureFixture[str],
):
    failure = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="/documents/",
        detail="the tablet did not answer",
    )
    bind(
        _FactsDouble(
            facts=_ANSWERED_FACTS,
            resources=_ANSWERED_RESOURCES,
            failure=failure,
        )
    )
    status = _device.info(json=True)
    document = _document(capsys.readouterr().out)
    assert status == exit_code(failure)
    assert status != 0
    assert document["type"] == "error"
    error: dict[str, Any] = document["error"]
    assert error["type"] == "DeviceUnreachable"
    assert error["exit_code"] == status
