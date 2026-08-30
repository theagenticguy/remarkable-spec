"""The shared command foundation: one invocation, one error boundary, one page policy."""

from __future__ import annotations

import io
import json as json_module
import os
from collections.abc import Iterator
from typing import get_args

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import PageSelection, ResolveDocument
from rmspec.cli._container import Feature
from rmspec.cli._invoke import (
    FEATURE_MODEL_BEDROCK,
    FEATURE_NAMES,
    FEATURE_PDF_READ,
    FEATURE_RASTER,
    FEATURE_SCENE_DECODE,
    PAGE_SPEC_GRAMMAR,
    DenseFlag,
    Invoked,
    JsonFlag,
    LimitOption,
    MaxPagesOption,
    PagesOption,
    StrictFlag,
    _collapsed,
    _open,
    invoked,
    page_cap,
    page_selection,
    render,
    report_degradations,
    resolve_document,
    run,
)
from rmspec.cli._output import CliOutput, OutputMode, make_console_pair
from rmspec.cli._settings import (
    HOMEBREW_LIBRARY_DIR,
    NATIVE_LIBRARY_PATH_VAR,
    CliSettings,
)
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import (
    AmbiguousDocument,
    Degradation,
    DegradationKind,
    InvalidSettingError,
    MissingDependencyError,
    PageNotFound,
    UsageError,
    exit_code,
)
from rmspec.domain.ports.device import DeviceCatalog, DeviceDocument, DeviceFileType
from rmspec.domain.ports.errors import DependencyProbe

_Finalized = Iterator
"""``_container.Finalized`` spelled locally, so ``Iterator`` keeps a runtime use.

dishka reads a generator provider's return annotation at container build, so the import cannot
move into an ``if TYPE_CHECKING:`` block -- and the repo allows neither a ``noqa`` nor a
``type: ignore``. This is the same alias, for the same reason, as ``_container.Finalized``.
"""

_OVERRIDDEN_PORTS = (DependencyProbe,)
"""The port :class:`_ProbeProvider` binds a double over, listed to give the import a runtime use.

Same reason as :data:`_Finalized`: dishka reads a provider's return annotation at container
build to learn what it provides, so moving the name into an ``if TYPE_CHECKING:`` block makes
the container raise instead of resolving. ``_container.BOUND_PORTS`` is the same discipline.
"""

_ONE = "a" * 8
_TWO = "b" * 8

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


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test in this file independent of the developer's own shell.

    ``load_settings`` reads the real environment, so an exported ``RMSPEC_*`` would change
    what a test measures -- or fail it outright, since a bad value is an
    ``InvalidSettingError``. Pinning the loader variable as well stops
    ``apply_native_library_path`` mutating the interpreter these tests run in.
    """
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


class _CatalogDouble(Provider):
    """Bind the shipped in-memory catalog over the real device binding.

    ``override=True`` on the one port every document-resolving use case reaches through is
    what keeps these tests off the wire: nothing in the resulting graph opens an SSH session
    or constructs a ``bedrock-runtime`` or ``textract`` client.
    """

    scope = Scope.REQUEST

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        return InMemoryDeviceCatalog(documents=_DOCUMENTS)


class _ProbeDouble:
    """A :class:`~rmspec.domain.ports.errors.DependencyProbe` that answers from two tables.

    Lets a test exercise the missing-extra path without uninstalling anything from the
    interpreter it runs in, which is the reason the port exists.
    """

    def __init__(self, *, absent: frozenset[str] = frozenset(), unloadable: str = "") -> None:
        self._absent = absent
        self._unloadable = unloadable
        self.asked: list[str] = []
        """Every module name the probe was asked about, so a test can assert it probed none."""

    def is_installed(self, module_name: str, /) -> bool:
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, module_name: str, /) -> str | None:
        if module_name == self._unloadable:
            return 'no library called "cairo-2" was found'
        return None


class _ProbeProvider(Provider):
    """Bind one :class:`_ProbeDouble` over the real ``ImportProbe``."""

    scope = Scope.APP

    def __init__(self, probe: _ProbeDouble) -> None:
        super().__init__()
        self._probe = probe

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        return self._probe


class _FinalizerProbe(Provider):
    """A request-scoped generator binding that records when its finalizer ran.

    Stands in for ``UsbWebApi`` and ``ParamikoShell``, both of which are bound this way. It
    is what makes "one command is one device handshake, closed by one finalizer" a measured
    claim rather than a comment.
    """

    scope = Scope.REQUEST

    def __init__(self, closed: list[str]) -> None:
        super().__init__()
        self._closed = closed

    @provide(override=True)
    def catalog(self) -> _Finalized[DeviceCatalog]:
        yield InMemoryDeviceCatalog(documents=_DOCUMENTS)
        self._closed.append("catalog")


def _writer(mode: OutputMode) -> tuple[CliOutput, io.StringIO, io.StringIO]:
    """Build a writer over two buffers so a test can read both streams."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    consoles = make_console_pair(stdout=stdout, stderr=stderr)
    return CliOutput(consoles=consoles, mode=mode), stdout, stderr


def _degradation(*, subject: str = "doc", detail: str = "why") -> Degradation:
    return Degradation(
        kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
        subject=subject,
        detail=detail,
    )


# ─────────────────────────── the --pages grammar ───────────────────────────


def test_pages_reads_a_single_index():
    assert page_selection(pages="4") == PageSelection.of(4)


def test_pages_reads_a_comma_list_and_normalises_it():
    assert page_selection(pages="3,1,1") == PageSelection.of(1, 3)


def test_pages_expands_an_inclusive_range():
    assert page_selection(pages="2-5") == PageSelection.of(2, 3, 4, 5)


def test_pages_expands_a_degenerate_range_to_one_index():
    assert page_selection(pages="7-7") == PageSelection.of(7)


def test_pages_mixes_indices_and_ranges_and_tolerates_whitespace():
    assert page_selection(pages=" 0 , 7 - 9 ") == PageSelection.of(0, 7, 8, 9)


def test_pages_drops_an_empty_item_so_a_trailing_comma_is_not_an_error():
    assert page_selection(pages="1,2,") == PageSelection.of(1, 2)


def test_pages_is_zero_based_so_the_first_page_is_index_zero():
    assert page_selection(pages="0") == PageSelection.of(0)


def test_pages_refuses_a_spec_that_names_no_index():
    with pytest.raises(UsageError) as caught:
        page_selection(pages=" , ")
    assert caught.value.requirement == PAGE_SPEC_GRAMMAR


def test_pages_refuses_a_non_numeric_item():
    with pytest.raises(UsageError, match="--pages"):
        page_selection(pages="first")


def test_pages_refuses_a_superscript_digit_int_would_reject():
    with pytest.raises(UsageError, match="--pages"):
        page_selection(pages="²")


def test_pages_refuses_a_negative_index():
    with pytest.raises(UsageError, match="--pages"):
        page_selection(pages="-3")


def test_pages_refuses_an_open_ended_range():
    with pytest.raises(UsageError, match="--pages"):
        page_selection(pages="2-")


def test_pages_refuses_a_descending_range_rather_than_reversing_it():
    with pytest.raises(UsageError) as caught:
        page_selection(pages="5-2")
    assert caught.value.requirement == "a range whose end is not below its start"


def test_limit_becomes_a_leading_bound():
    assert page_selection(limit=3) == PageSelection.first(3)


def test_no_page_flag_selects_every_page():
    assert page_selection() == PageSelection.all()


def test_limit_of_zero_is_a_usage_error_not_a_validation_error():
    with pytest.raises(UsageError, match="--limit 0"):
        page_selection(limit=0)


def test_pages_and_limit_together_are_refused_before_pydantic_sees_them():
    with pytest.raises(UsageError, match="--pages and --limit"):
        page_selection(pages="1", limit=2)


# ─────────────────────────────── the page cap ───────────────────────────────


def test_the_cap_comes_from_the_setting_when_no_flag_overrides_it():
    assert page_cap(CliSettings()) == 64


def test_the_cap_reads_the_environment_rather_than_a_literal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "7")
    assert page_cap(CliSettings()) == 7


def test_max_pages_overrides_the_setting():
    assert page_cap(CliSettings(), override=200) == 200


def test_a_cap_of_zero_is_refused_because_it_is_not_a_smaller_run():
    with pytest.raises(UsageError, match="--max-pages 0"):
        page_cap(CliSettings(), override=0)


# ───────────────────────── the ambiguity policy ─────────────────────────


def _resolver() -> ResolveDocument:
    return ResolveDocument(catalog=InMemoryDeviceCatalog(documents=_DOCUMENTS))


def test_the_default_accepts_the_auto_resolution_and_carries_the_degradation():
    result = resolve_document(_resolver(), query="Notes")
    assert result.chosen.uuid == _ONE
    assert tuple(candidate.uuid for candidate in result.also_matched) == (_TWO,)
    assert DegradationKind.AMBIGUOUS_AUTO_RESOLVED in {d.kind for d in result.degradations}


def test_strict_raises_ambiguous_document_carrying_the_other_matches():
    with pytest.raises(AmbiguousDocument) as caught:
        resolve_document(_resolver(), query="Notes", strict=True)
    assert caught.value.query == "Notes"
    assert tuple(candidate.uuid for candidate in caught.value.candidates) == (_TWO,)


def test_strict_is_silent_when_exactly_one_document_matched():
    result = resolve_document(_resolver(), query="Notes one", strict=True)
    assert result.chosen.uuid == _ONE
    assert result.also_matched == ()


# ──────────────────────── degradation rendering ────────────────────────


def test_json_mode_leaves_degradations_to_the_envelope():
    out, stdout, stderr = _writer(OutputMode.JSON)
    report_degradations(out, (_degradation(),))
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


def test_human_mode_writes_degradations_to_stderr():
    out, stdout, stderr = _writer(OutputMode.HUMAN)
    report_degradations(out, (_degradation(),))
    assert stdout.getvalue() == ""
    assert "catalog_entry_skipped" in stderr.getvalue()


def test_dense_mode_warns_on_stderr_so_its_record_stream_stays_homogeneous():
    out, stdout, stderr = _writer(OutputMode.DENSE)
    report_degradations(out, (_degradation(),))
    assert stdout.getvalue() == ""
    assert "catalog_entry_skipped" in stderr.getvalue()


def test_an_empty_tuple_writes_nothing_at_all():
    out, _stdout, stderr = _writer(OutputMode.HUMAN)
    report_degradations(out, ())
    assert stderr.getvalue() == ""


def test_identical_degradations_collapse_with_a_count_for_the_human():
    collapsed = _collapsed((_degradation(), _degradation(), _degradation()))
    assert len(collapsed) == 1
    assert collapsed[0].detail == "why (x3)"


def test_a_lone_degradation_keeps_its_detail_verbatim():
    collapsed = _collapsed((_degradation(),))
    assert collapsed[0].detail == "why"


def test_collapsing_preserves_first_occurrence_order():
    collapsed = _collapsed(
        (
            _degradation(subject="one"),
            _degradation(subject="two"),
            _degradation(subject="one"),
        )
    )
    assert [item.subject for item in collapsed] == ["one", "two"]


# ───────────────────── the dependency probe, before anything is paid for ─────────────────────


def test_the_mirrored_feature_names_are_exactly_the_containers_own():
    assert set(FEATURE_NAMES) == {member.value for member in Feature}
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))


def test_probing_no_feature_asks_the_probe_nothing():
    double = _ProbeDouble()

    def body(invocation: Invoked) -> int:
        invocation.probe()
        return 0

    assert run(body, providers=[_ProbeProvider(double)]) == 0
    assert double.asked == []


def test_probing_a_usable_feature_asks_only_that_features_modules():
    double = _ProbeDouble()

    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_SCENE_DECODE)
        return 0

    assert run(body, providers=[_ProbeProvider(double)]) == 0
    assert double.asked == ["rmscene"]


def test_a_missing_module_becomes_a_domain_error_naming_the_extra_not_the_module(
    capsys: pytest.CaptureFixture[str],
):
    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_PDF_READ)
        return 0

    status = run(
        body, json=True, providers=[_ProbeProvider(_ProbeDouble(absent=frozenset({"pymupdf"})))]
    )
    assert status == exit_code(
        MissingDependencyError(package="pymupdf", extra="render", feature="x")
    )
    document = json_module.loads(capsys.readouterr().out)
    assert document["error"]["type"] == "MissingDependencyError"
    assert "uv sync --extra render" in document["error"]["remediation"]


def test_two_missing_extras_are_both_reported_from_one_run(capsys: pytest.CaptureFixture[str]):
    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_PDF_READ, FEATURE_SCENE_DECODE)
        return 0

    double = _ProbeDouble(absent=frozenset({"pymupdf", "rmscene"}))
    assert run(body, providers=[_ProbeProvider(double)]) == 78
    captured = capsys.readouterr().err
    assert "pymupdf" in captured
    assert "also unusable: rmscene" in captured
    assert "uv sync --extra render" in captured


def test_the_raised_failure_is_the_first_by_package_whatever_order_was_selected(
    capsys: pytest.CaptureFixture[str],
):
    def forwards(invocation: Invoked) -> int:
        invocation.probe(FEATURE_PDF_READ, FEATURE_SCENE_DECODE)
        return 0

    def backwards(invocation: Invoked) -> int:
        invocation.probe(FEATURE_SCENE_DECODE, FEATURE_PDF_READ)
        return 0

    raised: list[str] = []
    for body in (forwards, backwards):
        assert (
            run(
                body,
                json=True,
                providers=[_ProbeProvider(_ProbeDouble(absent=frozenset({"pymupdf", "rmscene"})))],
            )
            == 78
        )
        raised.append(json_module.loads(capsys.readouterr().out)["error"]["message"])
    assert raised[0] == raised[1]
    assert "pymupdf" in raised[0]


def test_an_installed_but_unloadable_module_carries_the_loaders_own_message(
    capsys: pytest.CaptureFixture[str],
):
    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_RASTER)
        return 0

    double = _ProbeDouble(absent=frozenset({"PIL"}), unloadable="cairocffi")
    assert run(body, providers=[_ProbeProvider(double)]) == 78
    captured = capsys.readouterr().err
    assert "also unusable: cairocffi" in captured
    assert "cairo-2" in captured


def test_the_probe_runs_before_the_command_touches_a_use_case(
    capsys: pytest.CaptureFixture[str],
):
    reached: list[str] = []

    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_PDF_READ)
        reached.append("past the probe")  # pragma: no cover - must never run
        return 0

    assert run(body, providers=[_ProbeProvider(_ProbeDouble(absent=frozenset({"pymupdf"})))]) == 78
    assert reached == []
    assert "error:" in capsys.readouterr().err


def test_an_engine_feature_probes_its_backend_module():
    double = _ProbeDouble()

    def body(invocation: Invoked) -> int:
        invocation.probe(FEATURE_MODEL_BEDROCK)
        return 0

    assert run(body, providers=[_ProbeProvider(double)]) == 0
    assert double.asked == ["boto3"]


# ───────────────── the container-free boundary, for env and manifest ─────────────────


def test_render_runs_a_body_that_only_needs_the_writer(capsys: pytest.CaptureFixture[str]):
    def body(out: CliOutput) -> int:
        out.line("plain")
        return 0

    assert render(body) == 0
    assert capsys.readouterr().out == "plain\n"


def test_render_refuses_two_output_modes(capsys: pytest.CaptureFixture[str]):
    def body(_out: CliOutput) -> int:  # pragma: no cover - must never run
        pytest.fail("the body ran despite a contradictory mode")

    assert render(body, json=True, dense=True) == 2
    assert json_module.loads(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_render_is_the_same_error_boundary_as_run(capsys: pytest.CaptureFixture[str]):
    def body(_out: CliOutput) -> int:
        raise PageNotFound(document_uuid=_ONE, page="9")

    assert render(body, json=True) == 66
    assert json_module.loads(capsys.readouterr().out)["error"]["type"] == "PageNotFound"


def test_render_reads_no_setting_so_a_broken_environment_cannot_stop_it(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "nought")

    def body(_out: CliOutput) -> int:
        return 0

    assert render(body) == 0


# ─────────────────────────── the invocation ───────────────────────────


def test_run_returns_the_body_status_and_composes_a_request_scope(
    capsys: pytest.CaptureFixture[str],
):
    seen: list[Invoked] = []

    def body(invocation: Invoked) -> int:
        seen.append(invocation)
        return 0

    assert run(body) == 0
    assert seen[0].out.mode is OutputMode.HUMAN
    assert seen[0].settings.max_pages == 64
    assert capsys.readouterr().out == ""


def test_run_passes_the_output_mode_into_the_request_scope(capsys: pytest.CaptureFixture[str]):
    def body(invocation: Invoked) -> int:
        invocation.out.emit({"ok": True}, response_type="catalog")
        return 0

    assert run(body, json=True) == 0
    document = json_module.loads(capsys.readouterr().out)
    assert document["type"] == "catalog"


def test_run_renders_a_domain_error_and_returns_its_exit_status(
    capsys: pytest.CaptureFixture[str],
):
    def body(_invocation: Invoked) -> int:
        raise PageNotFound(document_uuid=_ONE, page="9")

    assert run(body, json=True) == 66
    document = json_module.loads(capsys.readouterr().out)
    assert document["error"]["type"] == "PageNotFound"


def test_run_refuses_two_output_modes_without_entering_a_container(
    capsys: pytest.CaptureFixture[str],
):
    def body(_invocation: Invoked) -> int:  # pragma: no cover - must never run
        pytest.fail("the body ran despite a contradictory mode")

    assert run(body, json=True, dense=True) == 2
    assert json_module.loads(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_run_renders_a_bad_setting_because_loading_is_inside_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "nought")

    def body(_invocation: Invoked) -> int:  # pragma: no cover - must never run
        pytest.fail("the body ran despite unloadable settings")

    assert run(body, json=True) == 78
    assert json_module.loads(capsys.readouterr().out)["error"]["type"] == "InvalidSettingError"


def test_invoked_raises_the_mode_refusal_rather_than_rendering_it():
    with pytest.raises(UsageError, match="--json and --dense"), invoked(json=True, dense=True):
        pytest.fail("the body ran despite a contradictory mode")


def test_the_request_finalizer_runs_when_the_body_returns():
    closed: list[str] = []

    def body(invocation: Invoked) -> int:
        invocation.get(ResolveDocument)
        return 0

    assert run(body, providers=[_FinalizerProbe(closed)]) == 0
    assert closed == ["catalog"]


def test_the_request_finalizer_runs_even_when_the_body_raises():
    closed: list[str] = []

    def failing() -> None:
        with invoked(providers=[_FinalizerProbe(closed)]) as invocation:
            invocation.get(ResolveDocument)
            raise PageNotFound(document_uuid=_ONE, page="9")

    with pytest.raises(PageNotFound):
        failing()
    assert closed == ["catalog"]


def test_invoked_resolves_a_use_case_out_of_the_request_scope():
    with invoked(providers=[_CatalogDouble()]) as invocation:
        assert isinstance(invocation.get(ResolveDocument), ResolveDocument)


def test_the_invocation_delegates_the_document_policy(capsys: pytest.CaptureFixture[str]):
    def body(invocation: Invoked) -> int:
        result = invocation.document("Notes")
        assert result.chosen.uuid == _ONE
        invocation.report(result.degradations)
        return 0

    assert run(body, providers=[_CatalogDouble()]) == 0
    assert "ambiguous_auto_resolved" in capsys.readouterr().err


def test_the_invocation_delegates_strictness_to_the_same_policy():
    def body(invocation: Invoked) -> int:  # pragma: no cover - returns via the boundary
        invocation.document("Notes", strict=True)
        return 0

    assert run(body, providers=[_CatalogDouble()]) == 2


def test_the_invocation_delegates_the_selection_and_the_cap():
    def body(invocation: Invoked) -> int:
        assert invocation.selection(pages="1-2") == PageSelection.of(1, 2)
        assert invocation.max_pages() == 64
        assert invocation.max_pages(5) == 5
        return 0

    assert run(body) == 0


def test_load_settings_failures_are_domain_errors_not_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "nought")
    with pytest.raises(InvalidSettingError), invoked():
        pytest.fail("the body ran despite unloadable settings")


# ───────────────────────── the declared flag surface ─────────────────────────


def test_open_reports_the_refusal_rather_than_raising_it():
    opened = _open(json=True, dense=True)
    assert isinstance(opened.refusal, UsageError)
    assert opened.out.mode is OutputMode.JSON


def test_open_reports_no_refusal_for_a_single_mode():
    assert _open(json=False, dense=False).refusal is None


@pytest.mark.parametrize(
    ("alias", "flag"),
    [
        (JsonFlag, "--json"),
        (DenseFlag, "--dense"),
        (StrictFlag, "--strict"),
        (PagesOption, "--pages"),
        (LimitOption, "--limit"),
        (MaxPagesOption, "--max-pages"),
    ],
)
def test_every_flag_alias_names_its_own_spelling(alias: object, flag: str):
    assert get_args(alias)[1].name == (flag,)


@pytest.mark.parametrize("alias", [JsonFlag, DenseFlag, StrictFlag])
def test_no_boolean_flag_generates_a_negative_cyclopts_would_accept_silently(alias: object):
    assert get_args(alias)[1].negative == ()
