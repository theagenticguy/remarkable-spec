"""Settings: the defaults that were measured, and the two silent-discard holes closed."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

import rmspec.cli
from rmspec.cli._settings import (
    HOMEBREW_LIBRARY_DIR,
    NATIVE_LIBRARY_PATH_VAR,
    CliSettings,
    OcrEngineName,
    Transport,
    _UnknownPrefixedEnvVars,
    apply_native_library_path,
    load_settings,
)
from rmspec.domain.errors import InvalidSettingError, exit_code
from rmspec.domain.models import PAPER_PRO_SCREEN, RM2_SCREEN

_EX_CONFIG = 78
"""``EX_CONFIG``, the status the domain's table gives a ``ConfigurationError``."""


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in an empty directory with no inherited ``RMSPEC_*`` state.

    ``env_file=".env"`` is resolved against the working directory, so a ``.env`` at the
    repository root would otherwise reach into these tests, and a developer's exported
    ``RMSPEC_XOCHITL`` would make the defaults unassertable.
    """
    for name in tuple(CliSettings.model_fields):
        monkeypatch.delenv(f"RMSPEC_{name.upper()}", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def test_defaults_are_the_measured_values():
    settings = load_settings()

    assert settings.xochitl is None
    assert settings.device_host == "10.11.99.1"
    assert settings.device_user == "root"
    assert settings.render_dpi == 229
    assert settings.ocr_dpi == 300
    assert settings.thickness == 1.5
    assert settings.max_pages == 64
    assert settings.transport is Transport.USB
    assert settings.aws_region == "us-west-2"
    assert settings.read_model == "global.openai.gpt-5.6-luna"
    assert settings.merge_model == "global.openai.gpt-5.6-terra"
    assert settings.ocr_engines == frozenset({OcrEngineName.BDA})
    assert settings.bda_project_arn is None
    assert settings.bda_profile == "us.data-automation-v1"
    assert settings.agreement_threshold == 0.90


def test_ssh_key_defaults_to_the_path_that_actually_authenticates(tmp_path: Path):
    # ParamikoShell(key_path=None) raises DeviceAuthFailed against a device `ssh
    # remarkable` reaches, because paramiko does not read ~/.ssh/config. A None default
    # would fail every SSH command on a correctly configured machine.
    assert load_settings().ssh_key == tmp_path / ".ssh" / "id_ed25519_remarkable"


def test_sync_db_defaults_to_the_legacy_mirror_location(tmp_path: Path):
    assert load_settings().sync_db == tmp_path / ".remarkable-spec" / "sync.db"


def test_the_home_directory_is_read_when_settings_are_built_not_at_import(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    first = load_settings().ssh_key
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))

    assert load_settings().ssh_key != first


def test_device_password_is_not_a_setting():
    # ParamikoShell has no password authentication path at all, so the legacy setting was
    # a promise the code could not keep.
    assert "device_password" not in CliSettings.model_fields


def test_dpi_is_split_into_two_settings_and_the_single_name_is_gone():
    # Legacy declared RMSPEC_DPI at 226 with zero readers while three commands hardcoded
    # 300. Those are different quantities, so wiring one name through would have silently
    # downgraded every OCR raster.
    assert "dpi" not in CliSettings.model_fields
    assert CliSettings.model_fields["render_dpi"].default == 229
    assert CliSettings.model_fields["ocr_dpi"].default == 300


def test_the_render_default_is_the_paper_pro_panels_own_density():
    """The default equals the domain's panel, read from it rather than retyped beside it.

    This is the assertion whose absence let 226 -- ``RM2_SCREEN``'s density, the reMarkable 1
    and 2 panel -- survive as the Paper Pro's default through the legacy ``RMSPEC_DPI`` that
    had zero readers, rendering every default page 1599x2132 instead of 1620x2160. Reading
    ``PAPER_PRO_SCREEN.dpi`` rather than writing 229 is the point: the setting and the panel
    it claims to match cannot drift apart without failing here, and a default borrowed from
    another device fails here the moment it is declared.
    """
    assert CliSettings.model_fields["render_dpi"].default == PAPER_PRO_SCREEN.dpi
    assert CliSettings.model_fields["render_dpi"].default != RM2_SCREEN.dpi


_OVERRIDES: list[tuple[str, str, str, object]] = [
    ("RMSPEC_XOCHITL", "/data/xochitl", "xochitl", Path("/data/xochitl")),
    ("RMSPEC_DEVICE_HOST", "192.168.1.5", "device_host", "192.168.1.5"),
    ("RMSPEC_DEVICE_USER", "someone", "device_user", "someone"),
    ("RMSPEC_SSH_KEY", "/keys/rm", "ssh_key", Path("/keys/rm")),
    ("RMSPEC_SYNC_DB", "/db/sync.db", "sync_db", Path("/db/sync.db")),
    ("RMSPEC_RENDER_DPI", "300", "render_dpi", 300),
    ("RMSPEC_OCR_DPI", "400", "ocr_dpi", 400),
    ("RMSPEC_THICKNESS", "2.0", "thickness", 2.0),
    ("RMSPEC_MAX_PAGES", "8", "max_pages", 8),
    ("RMSPEC_TRANSPORT", "ssh", "transport", Transport.SSH),
    ("RMSPEC_AWS_REGION", "eu-west-1", "aws_region", "eu-west-1"),
    ("RMSPEC_READ_MODEL", "some.reader", "read_model", "some.reader"),
    ("RMSPEC_MERGE_MODEL", "some.judge", "merge_model", "some.judge"),
    (
        "RMSPEC_BDA_PROJECT_ARN",
        "arn:aws:bedrock:eu-west-1:123456789012:data-automation-project/abc",
        "bda_project_arn",
        "arn:aws:bedrock:eu-west-1:123456789012:data-automation-project/abc",
    ),
    ("RMSPEC_BDA_PROFILE", "eu.data-automation-v1", "bda_profile", "eu.data-automation-v1"),
    (
        "RMSPEC_OCR_ENGINES",
        "apple_vision",
        "ocr_engines",
        frozenset({OcrEngineName.APPLE_VISION}),
    ),
    ("RMSPEC_AGREEMENT_THRESHOLD", "0.5", "agreement_threshold", 0.5),
]
"""One row per setting: the variable, a value to export, the attribute, and what it becomes.

A module constant rather than an inline ``parametrize`` list so that
``test_every_declared_setting_appears_in_the_override_table`` can read it as data. Reaching it
back off the decorated function's ``pytestmark`` worked and does not typecheck, which is the
right verdict: the table is data both tests need, not a private detail of one of them.
"""


@pytest.mark.parametrize(("variable", "value", "attribute", "expected"), _OVERRIDES)
def test_every_setting_is_overridable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    attribute: str,
    expected: object,
):
    monkeypatch.setenv(variable, value)

    assert getattr(load_settings(), attribute) == expected


def test_every_declared_setting_appears_in_the_override_table():
    # The table above is only a proof that every setting is reachable from the environment if
    # it names every setting, so the two are pinned to each other rather than to a count.
    named = {variable for variable, _, _, _ in _OVERRIDES}

    assert named == {f"RMSPEC_{name.upper()}" for name in CliSettings.model_fields}


def test_the_new_settings_are_declared_after_device_host():
    # model_fields is declaration-ordered and `rmspec env` renders it in that order, so a
    # field declared before device_host would move the first line of an evaluable export block.
    order = tuple(CliSettings.model_fields)

    assert order.index("device_host") < order.index("max_pages")
    assert order[-9:] == (
        "max_pages",
        "transport",
        "aws_region",
        "read_model",
        "merge_model",
        "bda_project_arn",
        "bda_profile",
        "ocr_engines",
        "agreement_threshold",
    )


def test_a_comma_separated_engine_list_is_read_as_a_set(monkeypatch: pytest.MonkeyPatch):
    # pydantic-settings decodes a complex field's environment value as JSON unless NoDecode
    # says otherwise, and a SettingsError is not a ValidationError -- so without that
    # annotation this raises past load_settings' translation entirely.
    monkeypatch.setenv("RMSPEC_OCR_ENGINES", " bda , textract , apple_vision ,")

    assert load_settings().ocr_engines == frozenset(OcrEngineName)


def test_an_engine_set_passed_to_the_constructor_is_left_alone():
    settings = CliSettings(ocr_engines=frozenset({OcrEngineName.APPLE_VISION}))

    assert settings.ocr_engines == frozenset({OcrEngineName.APPLE_VISION})


def test_an_unknown_engine_name_fails_at_startup_naming_the_ones_that_exist(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_OCR_ENGINES", "textrct")

    with pytest.raises(InvalidSettingError) as caught:
        load_settings()

    assert caught.value.setting == "RMSPEC_OCR_ENGINES"
    assert "'textract'" in caught.value.requirement
    assert exit_code(caught.value) == _EX_CONFIG


def test_a_mistyped_environment_variable_fails_at_startup(monkeypatch: pytest.MonkeyPatch):
    # The legacy hole: extra="ignore" discarded this, the user got the default xochitl
    # root, and the symptom was "the tool cannot see my documents".
    monkeypatch.setenv("RMSPEC_XOCHITLE", "/x")

    with pytest.raises(InvalidSettingError) as caught:
        load_settings()

    assert caught.value.setting == "RMSPEC_XOCHITLE"
    assert "the closest match is RMSPEC_XOCHITL" in caught.value.message
    assert exit_code(caught.value) == _EX_CONFIG


def test_extra_forbid_alone_would_not_have_caught_the_orphan(monkeypatch: pytest.MonkeyPatch):
    # Measured on pydantic-settings 2.13.1: EnvSettingsSource iterates the declared fields
    # and asks the environment for each, so a prefixed name no field claims is never
    # offered to the model and extra="forbid" never sees it. Only the dotenv source scans
    # the other way. This source is what closes the gap, and it reports orphans only.
    monkeypatch.setenv("RMSPEC_XOCHITLE", "/x")
    monkeypatch.setenv("RMSPEC_DEVICE_HOST", "192.168.1.5")

    assert _UnknownPrefixedEnvVars(CliSettings)() == {"xochitle": "/x"}


def test_a_mistyped_dotenv_variable_is_named_with_one_prefix(tmp_path: Path):
    # Regression: the dotenv source reports the field name with the prefix still attached
    # while the environment source strips it, so the first version of this reported
    # "RMSPEC_RMSPEC_XOCHITLE".
    (tmp_path / ".env").write_text("RMSPEC_XOCHITLE=/x\n", encoding="utf-8")

    with pytest.raises(InvalidSettingError) as caught:
        load_settings()

    assert caught.value.setting == "RMSPEC_XOCHITLE"
    assert "the closest match is RMSPEC_XOCHITL" in caught.value.message


def test_an_unrecognised_name_with_no_near_miss_says_so_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_ZZZZZZZZZZ", "1")

    with pytest.raises(InvalidSettingError) as caught:
        load_settings()

    assert caught.value.requirement == "a setting name this CLI reads"


@pytest.mark.parametrize(
    ("variable", "value", "requirement"),
    [
        ("RMSPEC_RENDER_DPI", "0", "greater than 0"),
        ("RMSPEC_OCR_DPI", "-1", "greater than 0"),
        ("RMSPEC_THICKNESS", "0", "greater than 0"),
        ("RMSPEC_RENDER_DPI", "abc", "a valid integer, unable to parse string as an integer"),
        ("RMSPEC_MAX_PAGES", "0", "greater than 0"),
        ("RMSPEC_AGREEMENT_THRESHOLD", "1.5", "less than or equal to 1"),
        ("RMSPEC_AGREEMENT_THRESHOLD", "-0.1", "greater than or equal to 0"),
        ("RMSPEC_TRANSPORT", "usbb", "'usb', 'ssh' or 'mirror'"),
    ],
)
def test_an_unusable_value_leaves_as_a_domain_error(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    requirement: str,
):
    monkeypatch.setenv(variable, value)

    with pytest.raises(InvalidSettingError) as caught:
        load_settings()

    assert caught.value.setting == variable
    assert caught.value.requirement == requirement
    assert exit_code(caught.value) == _EX_CONFIG


def test_the_loader_path_is_set_on_macos_when_nothing_else_claimed_it():
    environ: dict[str, str] = {}

    assert apply_native_library_path(environ, platform="darwin") == HOMEBREW_LIBRARY_DIR
    assert environ[NATIVE_LIBRARY_PATH_VAR] == HOMEBREW_LIBRARY_DIR


def test_an_existing_loader_path_outranks_this_default():
    environ = {NATIVE_LIBRARY_PATH_VAR: "/opt/mine/lib"}

    assert apply_native_library_path(environ, platform="darwin") is None
    assert environ[NATIVE_LIBRARY_PATH_VAR] == "/opt/mine/lib"


def test_the_loader_path_is_untouched_off_macos():
    environ: dict[str, str] = {}

    assert apply_native_library_path(environ, platform="linux") is None
    assert environ == {}


def test_importing_the_cli_does_not_mutate_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    # Legacy assigned os.environ["DYLD_FALLBACK_LIBRARY_PATH"] as an import side effect of
    # the CLI package, so collecting a test, running a completion or printing --version
    # changed the environment of the process that did it.
    monkeypatch.delenv(NATIVE_LIBRARY_PATH_VAR, raising=False)
    # Record `rmspec.cli` on its parent package before the re-import replaces it. monkeypatch
    # restores sys.modules entries but nothing sets the parent's attribute back, and
    # `monkeypatch.setattr("rmspec.cli.X", ...)` resolves X by walking getattr from `rmspec` --
    # not by reading sys.modules. So without this line every later test in the session that
    # patches a `rmspec.cli` attribute by dotted string patches this test's throwaway module
    # while the code under test keeps reading the original, and the patch silently does
    # nothing. Measured: it turned test_cli_entry's "refuses to invent a version" assertion
    # green-when-broken depending only on which file pytest-randomly ordered first.
    monkeypatch.setattr(rmspec, "cli", rmspec.cli)
    for name in [name for name in sys.modules if name.startswith("rmspec.cli")]:
        monkeypatch.delitem(sys.modules, name)

    importlib.import_module("rmspec.cli")

    assert NATIVE_LIBRARY_PATH_VAR not in os.environ
