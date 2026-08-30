"""The entry point: the version, the two commands, and the adapter-free ``--help``."""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import pytest

import rmspec.cli
from rmspec.app import (
    RefusedOperation,
    ReportCapabilities,
    ReportCapabilitiesResult,
)
from rmspec.cli import (
    CAPABILITIES_RESPONSE_TYPE,
    DISTRIBUTIONS,
    SETTINGS_RESPONSE_TYPE,
    _capability_table,
    _container,
    _installed_version,
    _remedy,
    _shell_value,
    _shell_variables,
    _version,
    app,
    doctor,
    env,
    resolved_version,
)
from rmspec.cli._container import DependencyFailure, describe_bindings
from rmspec.cli._output import API_VERSION, ERROR_RESPONSE_TYPE
from rmspec.cli._settings import CliSettings, load_settings
from rmspec.domain.errors import TransportKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec

_EX_CONFIG = 78
_EX_USAGE = 2

_ADAPTER_SLICES = frozenset(
    {"device", "export", "formats", "ocr", "persistence", "render"},
)
"""The six adapter slices under the ``rmspec`` namespace. ``app`` and ``domain`` are pure."""

_ADAPTER_THIRD_PARTY = frozenset(
    {
        "boto3",
        "botocore",
        "cairocffi",
        "cairosvg",
        "fitz",
        "httpx",
        "paramiko",
        "PIL",
        "Quartz",
        "rmscene",
        "sqlite3",
    },
)
"""Every third-party module `tests/architecture` assigns to an adapter package, plus sqlite3."""

_CLI_SOURCE = pathlib.Path(__file__).parents[1] / "src" / "rmspec" / "cli"


def _is_adapter(module_name: str, /) -> bool:
    """Report whether importing this name would pull an adapter into the process."""
    head, _, rest = module_name.partition(".")
    if head == "rmspec":
        return rest.partition(".")[0] in _ADAPTER_SLICES
    return head in _ADAPTER_THIRD_PARTY


class _RefuseAdapters:
    """A meta-path finder that fails the test if anything asks for an adapter.

    The honest way to measure "``--help`` imports no adapter" from inside a process that has
    already imported them for other tests: drop them from ``sys.modules`` and refuse to let them
    back in. A module-count delta would silently pass once another test had warmed the cache.
    """

    def __init__(self) -> None:
        self.refused: list[str] = []

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: object = None,
    ) -> ModuleSpec | None:
        """Record an adapter import; defer every lookup to the real finders behind this one.

        ``path`` and ``target`` are part of the ``MetaPathFinder`` contract and unused here,
        because this finder answers about the name alone.
        """
        del path, target
        if _is_adapter(fullname):
            self.refused.append(fullname)
        return None


def _statically_imported(module_path: pathlib.Path, /) -> set[str]:
    """Collect every module a file imports with an ``import`` statement."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(module_path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def _dense_records(stdout: str, /) -> list[list[str]]:
    """Split a ``DENSE`` stream back into its header and its records."""
    return [line.split("\t") for line in stdout.splitlines()]


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a clean environment and a writable store location."""
    for name in tuple(CliSettings.model_fields):
        monkeypatch.delenv(f"RMSPEC_{name.upper()}", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RMSPEC_SYNC_DB", str(tmp_path / "sync.db"))
    monkeypatch.chdir(tmp_path)


# ------------------------------------------------------------------------- the app


def test_the_entry_point_name_is_the_one_the_scripts_table_declares():
    # [project.scripts] says rmspec = "rmspec.cli:app".
    assert app.name == ("rmspec",)


def test_version_reports_the_distribution_that_ships_this_cli():
    # Legacy asked about "remarkable-spec", which does not exist in this workspace, inside a
    # try that swallowed PackageNotFoundError and printed 0.0.0-dev.
    assert DISTRIBUTIONS == ("rmspec", "rmspec-cli")
    assert version("rmspec-cli") != "0.0.0-dev"


def test_version_falls_through_to_the_member_name_inside_this_workspace():
    # The bundled distribution `rmspec` is what users install; it is not installed here, so
    # the fallthrough is the path this venv exercises and the wheel's path is the other one.
    assert _installed_version("rmspec") is None
    assert _version() == version("rmspec-cli")


def test_version_refuses_to_invent_one_when_no_distribution_is_installed(
    monkeypatch: pytest.MonkeyPatch,
):
    # The whole point of the tuple is that a *single* miss is normal. All of them missing is a
    # broken install, and legacy answered that with the plausible-looking string 0.0.0-dev.
    #
    # The target is the module object this file imported, not the string "rmspec.cli.
    # DISTRIBUTIONS": monkeypatch resolves a dotted string by walking getattr from `rmspec`,
    # and test_cli_settings re-imports the package, so that walk can land on a different module
    # object than the `_version` imported above reads its globals from. The patch then applies
    # to a module nothing calls, `_version` returns rmspec-cli's real version, and the
    # assertion passes or fails on file order alone.
    monkeypatch.setattr(rmspec.cli, "DISTRIBUTIONS", ("rmspec-not-installed",))
    with pytest.raises(PackageNotFoundError, match="rmspec-not-installed"):
        _version()


def test_version_is_printed_from_installed_metadata(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        app(["--version"])

    assert capsys.readouterr().out.strip() == version("rmspec-cli")


def test_the_version_a_serialiser_reads_is_a_string_and_not_a_function_repr():
    # Measured: App(version=...) accepts a callable and cyclopts calls it lazily, so
    # app.version is a function object and str(app.version) is
    # "<function _version at 0x104ba1580>". `rmspec --version` is right because cyclopts
    # calls it; the trap is the next reader, and the manifest's "version" field is one.
    assert callable(app.version)
    assert app.version is _version
    assert resolved_version() == version("rmspec-cli")


def test_help_imports_no_adapter(monkeypatch: pytest.MonkeyPatch):
    finder = _RefuseAdapters()
    for name in [name for name in sys.modules if _is_adapter(name)]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

    with pytest.raises(SystemExit) as caught:
        app(["--help"])

    assert caught.value.code == 0
    assert finder.refused == []


def test_no_module_under_rmspec_cli_imports_an_adapter_at_module_scope():
    # The static half of the same claim, which also covers a module no command has reached
    # yet. _container is excluded: it is the composition root and adapters are its job.
    offenders = {
        path.name: sorted(name for name in _statically_imported(path) if _is_adapter(name))
        for path in sorted(_CLI_SOURCE.glob("*.py"))
        if path.name != "_container.py"
    }

    assert {name: found for name, found in offenders.items() if found} == {}


def test_the_container_is_the_one_module_that_names_adapters():
    imported = _statically_imported(_CLI_SOURCE / "_container.py")
    found = sorted(name for name in imported if _is_adapter(name))

    assert found


# ----------------------------------------------------------------- the boolean flags


@pytest.mark.parametrize("command", ["doctor", "env"])
@pytest.mark.parametrize("negative", ["--no-json", "--no-dense"])
def test_an_auto_generated_negative_flag_is_not_part_of_the_surface(
    command: str,
    negative: str,
    capsys: pytest.CaptureFixture[str],
):
    # Measured on cyclopts 4.6.0 before this was closed: `rmspec doctor --no-json` parsed
    # cleanly to {'json': False} and was documented nowhere -- not in --help, not in the
    # manifest that will enumerate this surface. An agent-facing CLI must not accept a flag
    # its own manifest will not list, so every boolean here sets Parameter(negative="").
    with pytest.raises(SystemExit) as caught:
        app([command, negative])

    assert caught.value.code != 0
    assert negative in capsys.readouterr().err


@pytest.mark.parametrize("command", ["doctor", "env"])
def test_the_positive_flags_are_still_accepted(command: str):
    _, bound, _ = app.parse_args([command, "--json"], exit_on_error=False)

    assert bound.kwargs == {"json": True}


def test_the_two_output_modes_are_mutually_exclusive(capsys: pytest.CaptureFixture[str]):
    assert doctor(json=True, dense=True) == _EX_USAGE

    document = json.loads(capsys.readouterr().out)
    assert document["type"] == ERROR_RESPONSE_TYPE
    assert document["error"]["type"] == "UsageError"
    assert document["error"]["exit_code"] == _EX_USAGE


def test_the_refusal_is_reported_before_anything_is_composed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # env never composes, so the honest way to say "the refusal short-circuits" is to make
    # the very next step explode and prove it was never reached.
    monkeypatch.setattr(
        _container,
        "probe_features",
        lambda _probe, _features: pytest.fail("the refusal did not short-circuit"),
    )

    assert doctor(json=True, dense=True) == _EX_USAGE
    assert env(json=True, dense=True) == _EX_USAGE

    assert capsys.readouterr().err.count("error: ") == 2


# ------------------------------------------------------------------------ rmspec env


def test_env_writes_assignments_a_shell_can_eval(capsys: pytest.CaptureFixture[str]):
    assert env(json=False) == 0

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == "export RMSPEC_DEVICE_HOST=10.11.99.1"
    assert all(line.startswith("export RMSPEC_") for line in lines)
    assert captured.err == ""


def test_the_exported_assignments_reproduce_the_settings_they_came_from(
    monkeypatch: pytest.MonkeyPatch,
):
    # Measured before _shell_value existed: `eval "$(rmspec env)"` followed by `rmspec env`
    # exited 78 rejecting its own exported RMSPEC_OCR_ENGINES, because str() of a set field
    # is "frozenset({<OcrEngineName.TEXTRACT: 'textract'>})" -- a repr, not a value. The
    # eval contract is the whole reason RMSPEC_* exists, so it is asserted rather than
    # assumed.
    original = load_settings()

    for name, value in _shell_variables(original).items():
        if value is not None:
            monkeypatch.setenv(name, value)

    assert load_settings() == original


def test_a_set_valued_setting_is_comma_separated_and_sorted():
    # Sorted, not merely joined: a frozenset's iteration order depends on the hash seed, and
    # an eval-able line that changes between runs is not reproducible.
    assert _shell_value(frozenset({"textract", "apple_vision"})) == "apple_vision,textract"


def test_env_quotes_a_value_a_shell_would_otherwise_split(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
):
    # rich wraps at 80 columns off a tty and eats [...] as markup, so legacy's env broke on
    # any path over about 65 characters. shlex.quote is what survives the space as well.
    awkward = tmp_path / "a path with [draft] brackets and a tail long enough to pass eighty"
    monkeypatch.setenv("RMSPEC_XOCHITL", str(awkward))

    assert env(json=False) == 0

    line = next(line for line in capsys.readouterr().out.splitlines() if "RMSPEC_XOCHITL" in line)
    assert line == f"export RMSPEC_XOCHITL='{awkward}'"


def test_env_omits_a_setting_that_is_unset(capsys: pytest.CaptureFixture[str]):
    # Exporting RMSPEC_XOCHITL='' would claim a mirror at the current directory.
    assert env(json=False) == 0

    assert "RMSPEC_XOCHITL" not in capsys.readouterr().out


def test_env_json_states_an_unset_setting_as_null(capsys: pytest.CaptureFixture[str]):
    # Updated for the step-7 envelope: the settings mapping moved from being the document's
    # top level to living under "data", so that a reader branches on "type" before it
    # touches the payload and a setting called "next" could never be mistaken for the hint.
    # Deliberate break of a command nothing has consumed yet -- design §1.4.
    assert env(json=True) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["api_version"] == API_VERSION
    assert document["type"] == SETTINGS_RESPONSE_TYPE
    assert document["degradations"] == []
    assert document["data"]["RMSPEC_XOCHITL"] is None
    assert document["data"]["RMSPEC_RENDER_DPI"] == "229"
    assert document["next"]["command"] == 'eval "$(rmspec env)"'


def test_env_dense_writes_one_record_per_set_setting(capsys: pytest.CaptureFixture[str]):
    assert env(dense=True) == 0

    captured = capsys.readouterr()
    header, *records = _dense_records(captured.out)
    assert header == ["variable", "value"]
    assert ["RMSPEC_DEVICE_HOST", "10.11.99.1"] in records
    assert all(len(record) == 2 for record in records)
    # The unset settings are omitted here exactly as they are from the export lines, and
    # --dense is not a tab-separated restatement of those lines: no `export`, no quoting.
    assert not any(record[0] == "RMSPEC_XOCHITL" for record in records)
    assert "export" not in captured.out
    assert captured.err == ""


def test_env_reports_a_bad_setting_as_a_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RMSPEC_OCR_DPI", "0")

    assert env(json=True) == _EX_CONFIG

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["type"] == ERROR_RESPONSE_TYPE
    assert document["error"]["type"] == "InvalidSettingError"
    assert "error: " in captured.err


def test_the_variable_names_round_trip_to_the_settings_they_came_from():
    variables = _shell_variables(CliSettings(sync_db=pathlib.Path("/db/s.db")))

    assert set(variables) == {f"RMSPEC_{name.upper()}" for name in CliSettings.model_fields}


# --------------------------------------------------------------------- rmspec doctor


@pytest.fixture
def _nothing_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the eager pass to "everything is usable".

    Without this, these tests assert on whichever extras this host happens to have installed,
    which makes a passing suite a statement about the machine rather than about the code. The
    real probe is exercised separately, and the failure rendering is exercised with a scripted
    one.
    """
    monkeypatch.setattr(_container, "probe_features", lambda _probe, _features: ())


def test_the_real_probe_runs_over_every_feature_without_raising():
    probe = _container.ImportProbe()

    failures = _container.probe_features(probe, tuple(_container.Feature))

    assert isinstance(failures, tuple)


@pytest.mark.usefixtures("_nothing_missing")
def test_doctor_emits_one_json_document_on_stdout(capsys: pytest.CaptureFixture[str]):
    # Updated for the step-7 envelope: the report moved under "data" and the frame's
    # api_version/type/degradations keys are now always present. The transport is USB
    # because RMSPEC_TRANSPORT defaults to "usb" (design §4) and USB is the default read
    # path (design §5) -- which is also why SearchIndexSource is reported as *limited*
    # rather than served: the firmware serves no file from the document tree over HTTP, so
    # tier 0 needs a shell session even on a USB run, and doctor's job is to say so.
    assert doctor(json=True) == 0

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["api_version"] == API_VERSION
    assert document["type"] == CAPABILITIES_RESPONSE_TYPE
    assert document["degradations"] == []
    assert document["data"]["transport"] == TransportKind.USB_WEB_API.value
    assert "DeviceCatalog" in document["data"]["served"]
    assert "SearchIndexSource" in [row["port"] for row in document["data"]["restricted"]]
    assert document["data"]["missing"] == []
    assert captured.err == ""


@pytest.mark.usefixtures("_nothing_missing")
def test_doctor_writes_the_table_to_stderr_and_nothing_to_stdout(
    capsys: pytest.CaptureFixture[str],
):
    assert doctor(json=False) == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DeviceCatalog" in captured.err


@pytest.mark.usefixtures("_nothing_missing")
def test_doctor_dense_carries_the_tables_information_without_the_box_drawing(
    capsys: pytest.CaptureFixture[str],
):
    assert doctor(dense=True) == 0

    captured = capsys.readouterr()
    header, *records = _dense_records(captured.out)
    assert header == ["port", "state", "detail"]
    assert ["DeviceCatalog", "served", ""] in records
    assert {record[1] for record in records} <= {"served", "limited", "unbound"}
    # Same row count as the table a human sees, which is the claim --dense makes.
    report = ReportCapabilities().report(describe_bindings())
    assert len(records) == _capability_table(report).row_count
    assert captured.err == ""


def test_doctor_teaches_every_missing_extra_from_one_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # The requirement the whole return-instead-of-raise split exists for.
    failures = (
        DependencyFailure(
            package="cairocffi",
            extra="render",
            feature="rasterizing a page",
            detail='no library called "cairo-2" was found',
        ),
        DependencyFailure(
            package="Quartz",
            extra="ocr",
            feature="recognising handwriting on device",
            detail=None,
        ),
    )
    monkeypatch.setattr(_container, "probe_features", lambda _probe, _features: failures)

    assert doctor(json=True) == _EX_CONFIG

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    # "missing" moved under "data" with the rest of the payload; "next" stays on the frame.
    assert [row["package"] for row in document["data"]["missing"]] == ["cairocffi", "Quartz"]
    assert document["next"]["command"] == "uv sync --extra ocr --extra render"
    # stderr is the human stream, so rich may wrap these lines; assert on the identifiers
    # rather than on a whole sentence.
    assert "cairocffi" in captured.err
    assert "Quartz" in captured.err


def test_doctor_reports_a_bad_setting_rather_than_composing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RMSPEC_THICKNESS", "-1")

    assert doctor(json=True) == _EX_CONFIG

    assert json.loads(capsys.readouterr().out)["error"]["type"] == "InvalidSettingError"


@pytest.mark.usefixtures("_nothing_missing")
def test_doctor_is_reachable_through_the_app(capsys: pytest.CaptureFixture[str]):
    # cyclopts turns a command's return value into the process exit status by raising
    # SystemExit with it, which is how the domain's exit_code table reaches the shell.
    with pytest.raises(SystemExit) as caught:
        app(["doctor", "--json"])

    assert caught.value.code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["data"]["transport"] == TransportKind.USB_WEB_API.value


def test_the_domains_exit_status_reaches_the_shell(monkeypatch: pytest.MonkeyPatch):
    # Legacy had only 0 and 1 across roughly sixty sys.exit(1) sites.
    monkeypatch.setenv("RMSPEC_RENDER_DPI", "0")

    with pytest.raises(SystemExit) as caught:
        app(["env", "--json"])

    assert caught.value.code == _EX_CONFIG


def test_no_remedy_is_offered_when_nothing_is_missing():
    assert _remedy(()) is None


def test_an_unbound_port_is_rendered_as_unbound():
    # describe_bindings() has no unbound port today, so the row type that reports one is
    # exercised directly rather than left uncovered until a binding disappears.
    report = ReportCapabilityFixture.unavailable_only()

    table = _capability_table(report)

    assert table.row_count == 1


class ReportCapabilityFixture:
    """Reports the real use case cannot currently produce, built by hand."""

    @staticmethod
    def unavailable_only() -> ReportCapabilitiesResult:
        """Give a report whose only row is an unbound port."""
        return ReportCapabilitiesResult(
            transport=TransportKind.LOCAL_MIRROR,
            served=(),
            restricted=(),
            unavailable=(
                RefusedOperation(
                    port="DocumentUploader",
                    operation="upload a document",
                    detail="a mirror is not a device",
                    supported_by=(TransportKind.USB_WEB_API, TransportKind.SSH),
                    refusal="upload a document is not possible over local_mirror",
                ),
            ),
            degradations=(),
        )


def test_the_capability_table_reports_every_row_the_use_case_produced():
    report = ReportCapabilities().report(describe_bindings())

    table = _capability_table(report)

    assert table.row_count == (
        len(report.served) + len(report.restricted) + len(report.unavailable)
    )
