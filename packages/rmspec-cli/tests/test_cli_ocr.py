"""What ``rmspec ocr`` must do: probe what it will use, cap the work, and report the cost.

No test here constructs a ``bedrock-runtime`` or ``textract`` client, opens a transport, or
rasterizes anything. :class:`~rmspec.app.TranscribePages` arrives as a stub bound with
``override=True``, which is what lets a test read the request the command built -- the only
place ``RMSPEC_OCR_DPI`` and ``--threshold`` can be proved to have been read.
"""

from __future__ import annotations

import functools
import json as json_module
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import (
    PageSelection,
    TranscribedPage,
    TranscribePages,
    TranscribePagesRequest,
    TranscribePagesResult,
)
from rmspec.cli import _ocr
from rmspec.cli._invoke import PAGE_SPEC_GRAMMAR, run
from rmspec.cli._ocr import _OCR_COLUMNS, ocr
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import Degradation, DegradationKind
from rmspec.domain.models import OcrProvenance, PageText, TextProvenance
from rmspec.domain.ports.device import DeviceCatalog, DeviceDocument, DeviceFileType
from rmspec.domain.ports.errors import DependencyProbe

if TYPE_CHECKING:
    from collections.abc import Mapping

_OVERRIDDEN_PORTS = (DependencyProbe,)
"""The port :class:`_ProbeProvider` binds a double over, listed to give it a runtime use.

dishka reads a provider's return annotation at container build to learn what it provides,
so moving the name into an ``if TYPE_CHECKING:`` block makes the container raise instead of
resolving -- and this repo allows neither a ``noqa`` nor a ``type: ignore``.
"""

_ONE = "a" * 8
_TWO = "b" * 8

_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

_DOCUMENTS = (
    DeviceDocument(
        uuid=_ONE,
        name="Notes one",
        file_type=DeviceFileType.NOTEBOOK,
        page_count=3,
    ),
    DeviceDocument(
        uuid=_TWO,
        name="Notes two",
        file_type=DeviceFileType.NOTEBOOK,
        page_count=1,
    ),
)

_TORN_INDEX = Degradation(
    kind=DegradationKind.DEVICE_INDEX_UNAVAILABLE,
    subject="page-ref-0",
    detail="the device handwriting index is unusable, so tier 0 was skipped",
)


def _page(
    index: int,
    /,
    *,
    text: str,
    tier: int,
    short_circuited: bool = False,
    cached: bool = False,
    cached_tier: int | None = None,
    truncated: bool = False,
    failures: Mapping[str, str] | None = None,
    confidence: float | None = None,
) -> TranscribedPage:
    """Build one transcribed page in whichever of the reportable states a test needs."""
    provenance = (
        None
        if cached_tier is None
        else OcrProvenance(
            tier_reached=cached_tier,
            short_circuited=cached_tier == 1,
            contributors=("index@1",),
            agreement=0.97 if cached_tier == 1 else None,
        )
    )
    return TranscribedPage(
        page=PageText(
            doc_uuid=_ONE,
            page_uuid=f"page-ref-{index}",
            page_index=index,
            text=text,
            provenance=TextProvenance(
                recognizers=("engine@1",),
                model_fingerprint=None,
                render_dpi=300,
                extracted_at=_NOW,
            ),
        ),
        tier_reached=tier,
        short_circuited=short_circuited,
        cached=cached,
        cached_provenance=provenance,
        truncated=truncated,
        recognizer_failures={} if failures is None else failures,
        mean_confidence=confidence,
    )


_PAGES = (
    _page(0, text="merged reading", tier=3, confidence=0.5),
    _page(1, text="cached reading", tier=0, cached=True, cached_tier=1),
    _page(
        2,
        text="short reading",
        tier=1,
        short_circuited=True,
        truncated=True,
        failures={"second@2": "bang", "first@1": "boom"},
        confidence=0.25,
    ),
)
"""One page per reportable state, so one run exercises every cell this command projects."""


def _result(*, degradations: tuple[Degradation, ...] = ()) -> TranscribePagesResult:
    """Build a transcription result over :data:`_PAGES`."""
    return TranscribePagesResult(
        document_uuid=_ONE,
        pages=_PAGES,
        render_digest="d" * 64,
        degradations=degradations,
    )


class _StubTranscribe:
    """A :class:`~rmspec.app.TranscribePages` that records requests and reads nothing."""

    def __init__(self, result: TranscribePagesResult) -> None:
        self.result = result
        self.requests: list[TranscribePagesRequest] = []

    def transcribe(self, request: TranscribePagesRequest, /) -> TranscribePagesResult:
        """Record the request and answer with the fixed result."""
        self.requests.append(request)
        return self.result


class _ProbeDouble:
    """A :class:`~rmspec.domain.ports.errors.DependencyProbe` answering from one set."""

    def __init__(self, *, absent: frozenset[str] = frozenset()) -> None:
        self._absent = absent
        self.asked: list[str] = []

    def is_installed(self, module_name: str, /) -> bool:
        """Report whether the module was left out of this double's world."""
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, module_name: str, /) -> str | None:
        """Report that every installed module loads, which keeps the probe honest."""
        assert module_name not in self._absent
        return None


class _TranscribeProvider(Provider):
    """Bind the stub over the real, model-backed use case."""

    scope = Scope.REQUEST

    def __init__(self, stub: _StubTranscribe) -> None:
        super().__init__()
        self._stub = stub

    @provide(override=True)
    def transcribe(self) -> TranscribePages:
        """Answer with the stub, cast because it stands in rather than subclasses."""
        return cast("TranscribePages", self._stub)


class _CatalogProvider(Provider):
    """Bind the shipped in-memory catalog, so nothing here opens a transport."""

    scope = Scope.REQUEST

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        """Answer with two documents whose names share a substring."""
        return InMemoryDeviceCatalog(documents=_DOCUMENTS)


class _ProbeProvider(Provider):
    """Bind a probe that answers from a table instead of from the interpreter."""

    scope = Scope.APP

    def __init__(self, probe: _ProbeDouble) -> None:
        super().__init__()
        self._probe = probe

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        """Answer with the double."""
        return self._probe


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test here independent of the developer's own exported ``RMSPEC_*``."""
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    stub: _StubTranscribe,
    /,
    *,
    probe: _ProbeDouble | None = None,
) -> None:
    """Put every override on the ``run`` the command module calls."""
    providers = [
        _TranscribeProvider(stub),
        _CatalogProvider(),
        _ProbeProvider(_ProbeDouble() if probe is None else probe),
    ]
    monkeypatch.setattr(_ocr, "run", functools.partial(run, providers=providers))


def _records(text: str, /) -> list[list[str]]:
    """Split ``DENSE`` output into its records."""
    return [line.split("\t") for line in text.splitlines()]


def _envelope(text: str, /) -> dict[str, Any]:
    """Parse the one JSON document a ``--json`` run writes to stdout."""
    parsed = json_module.loads(text)
    assert isinstance(parsed, dict)
    return parsed


def _failure(text: str, /) -> dict[str, Any]:
    """Take the ``error`` object out of a failure envelope."""
    error = _envelope(text)["error"]
    assert isinstance(error, dict)
    return error


def _sole(stub: _StubTranscribe, /) -> TranscribePagesRequest:
    """Take the one request the command built."""
    assert len(stub.requests) == 1
    return stub.requests[0]


def test_dense_reports_the_page_what_it_cost_and_what_it_says(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes one", dense=True)

    assert status == 0
    records = _records(capsys.readouterr().out)
    assert records[0] == list(_OCR_COLUMNS)
    assert records[1] == ["0", "3", "false", "false", "", "false", "0.500", "", "merged reading"]
    assert records[2] == ["1", "0", "false", "true", "1", "false", "", "", "cached reading"]
    assert records[3] == [
        "2",
        "1",
        "true",
        "false",
        "",
        "true",
        "0.250",
        "first@1=boom;second@2=bang",
        "short reading",
    ]


def test_json_carries_the_whole_transcription_result(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes one", json=True)

    assert status == 0
    document = _envelope(capsys.readouterr().out)
    assert document["type"] == "transcription"
    assert document["degradations"] == []
    data = document["data"]
    assert data["render_digest"] == "d" * 64
    assert [page["tier_reached"] for page in data["pages"]] == [3, 0, 1]
    assert [page["short_circuited"] for page in data["pages"]] == [False, False, True]
    assert data["pages"][1]["cached"] is True
    assert data["pages"][1]["cached_provenance"]["tier_reached"] == 1
    assert data["pages"][0]["cached_provenance"] is None
    assert data["pages"][2]["recognizer_failures"] == {"first@1": "boom", "second@2": "bang"}


def test_human_mode_puts_its_table_on_stderr_and_writes_nothing_to_stdout(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes one")

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == ""
    assert "merged" in captured.err


def test_the_density_is_the_ocr_setting_and_never_the_render_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_OCR_DPI", "222")
    monkeypatch.setenv("RMSPEC_RENDER_DPI", "111")
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one") == 0
    assert _sole(stub).render.raster_dpi == 222


def test_the_render_always_asks_for_pixels_at_the_default_recognition_density(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one") == 0
    assert _sole(stub).render.raster_dpi == 300


def test_the_threshold_comes_from_the_setting_and_the_flag_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_AGREEMENT_THRESHOLD", "0.42")
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one") == 0
    assert stub.requests[0].agreement_threshold == 0.42

    assert ocr("Notes one", threshold=0.99) == 0
    assert stub.requests[1].agreement_threshold == 0.99


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
def test_a_threshold_outside_zero_to_one_is_refused(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    threshold: float,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes one", threshold=threshold, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_the_probe_covers_the_selected_engine_and_not_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    probe = _ProbeDouble()
    _bind(monkeypatch, stub, probe=probe)

    assert ocr("Notes one") == 0
    assert "boto3" in probe.asked
    assert "Quartz" not in probe.asked
    assert "rmscene" in probe.asked
    assert "cairosvg" in probe.asked


def test_selecting_the_macos_engine_probes_its_backend_too(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_OCR_ENGINES", "apple_vision,textract")
    stub = _StubTranscribe(_result())
    probe = _ProbeDouble(absent=frozenset({"Quartz"}))
    _bind(monkeypatch, stub, probe=probe)

    status = ocr("Notes one", json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "MissingDependencyError"
    assert error["exit_code"] == status
    assert "Quartz" in probe.asked
    assert stub.requests == []


def test_the_probe_runs_before_the_command_touches_a_use_case(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub, probe=_ProbeDouble(absent=frozenset({"rmscene"})))

    status = ocr("Notes one", json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "MissingDependencyError"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_the_work_cap_comes_from_the_setting_and_the_flag_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "7")
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one") == 0
    assert stub.requests[0].render.max_pages == 7

    assert ocr("Notes one", max_pages=2) == 0
    assert stub.requests[1].render.max_pages == 2


def test_the_pages_flag_is_zero_based(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one", pages="0,2") == 0
    assert _sole(stub).render.selection == PageSelection.of(0, 2)


def test_the_leading_limit_is_a_selection_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    assert ocr("Notes one", limit=2) == 0
    assert _sole(stub).render.selection == PageSelection.first(2)


def test_the_help_text_repeats_the_shared_page_grammar() -> None:
    assert ocr.__doc__ is not None
    assert PAGE_SPEC_GRAMMAR in " ".join(ocr.__doc__.split())


def test_every_degradation_is_hoisted_into_the_envelope_in_order(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result(degradations=(_TORN_INDEX, _TORN_INDEX)))
    _bind(monkeypatch, stub)

    status = ocr("Notes", json=True)

    assert status == 0
    kinds = [item["kind"] for item in _envelope(capsys.readouterr().out)["degradations"]]
    assert kinds == [
        "ambiguous_auto_resolved",
        "device_index_unavailable",
        "device_index_unavailable",
    ]


def test_degradations_reach_stderr_in_dense_mode_and_are_collapsed_there(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result(degradations=(_TORN_INDEX, _TORN_INDEX)))
    _bind(monkeypatch, stub)

    status = ocr("Notes one", dense=True)

    captured = capsys.readouterr()
    assert status == 0
    assert "(x2)" in captured.err
    assert "device_index_unavailable" not in captured.out


def test_strict_refuses_an_ambiguous_selector_before_anything_is_transcribed(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes", strict=True, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "AmbiguousDocument"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_both_output_flags_together_are_refused(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubTranscribe(_result())
    _bind(monkeypatch, stub)

    status = ocr("Notes one", json=True, dense=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
