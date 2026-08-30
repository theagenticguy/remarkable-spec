"""``AGENTS.md``: that every fact in it is derived, and that the drift gate really bites."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import Any

import pytest

from rmspec.cli import app as root_app
from rmspec.cli._agents import (
    EXIT_CODE_MEANINGS,
    GENERATED_BANNER,
    OUTPUT_FLAGS,
    REGENERATE_TASK,
    _code,
    _home_relative,
    _inline,
    main,
    render_document,
)
from rmspec.cli._manifest import build_manifest
from rmspec.cli._output import API_VERSION, ERROR_RESPONSE_TYPE
from rmspec.domain.errors import Degradation, DegradationKind

REPO = Path(__file__).resolve().parents[3]
COMMITTED = REPO / "AGENTS.md"


@pytest.fixture(name="manifest_data")
def _manifest_data() -> dict[str, Any]:
    return build_manifest(root_app)


@pytest.fixture(name="document")
def _document(manifest_data: dict[str, Any]) -> str:
    return render_document(manifest_data)


# ─────────────── the committed file is the generated one ───────────────


def test_the_committed_document_is_exactly_what_the_manifest_renders(document: str):
    assert COMMITTED.is_file(), "AGENTS.md is not committed at the repository root"
    assert COMMITTED.read_text(encoding="utf-8") == document, (
        f"AGENTS.md has drifted from the manifest. Run `{REGENERATE_TASK}`."
    )


def test_the_document_says_it_is_generated_and_names_the_task_that_regenerates_it(document: str):
    assert document.splitlines()[0] == GENERATED_BANNER
    assert REGENERATE_TASK in GENERATED_BANNER


def test_the_document_ends_in_exactly_one_newline(document: str):
    assert document.endswith("\n")
    assert not document.endswith("\n\n")


# ─────────────── every section is present and complete ───────────────


def test_every_command_the_manifest_lists_appears_in_the_index(
    document: str,
    manifest_data: dict[str, Any],
):
    for command in manifest_data["commands"]:
        assert f"| `rmspec {command['name']}` |" in document


def test_every_response_type_a_command_can_emit_is_named(
    document: str,
    manifest_data: dict[str, Any],
):
    for command in manifest_data["commands"]:
        for response_type in command["response_types"]:
            assert f"`{response_type}`" in document
    assert "| `sync`, `history` |" in document


def test_every_parameter_that_is_not_an_output_flag_is_documented(
    document: str,
    manifest_data: dict[str, Any],
):
    for command in manifest_data["commands"]:
        for parameter in command["parameters"]:
            if OUTPUT_FLAGS.issuperset(parameter["flags"]):
                continue
            for flag in parameter["flags"]:
                assert _code(flag) in document, f"{command['name']} {flag} is undocumented"


def test_the_shared_output_flags_are_described_once_rather_than_per_command(document: str):
    assert document.count("| `--json` | stdout |") == 1
    assert "| `--json` | `bool` |" not in document


def test_every_error_identity_appears_with_its_exit_code(
    document: str,
    manifest_data: dict[str, Any],
):
    for entry in manifest_data["errors"]:
        assert f"| `{entry['type']}` | `{entry['exit_code']}` |" in document


def test_every_degradation_kind_is_listed_and_the_item_shape_is_named(document: str):
    for kind in DegradationKind:
        assert f"- `{kind.value}`" in document
    for field in Degradation.model_fields:
        assert f"`{field}`" in document


def test_every_setting_appears_with_its_variable_and_default(
    document: str,
    manifest_data: dict[str, Any],
):
    for entry in manifest_data["settings"]:
        assert f"| `{entry['name']}` |" in document


def test_both_envelope_examples_are_valid_json_carrying_the_real_keys(document: str):
    blocks = [block.split("```", 1)[0] for block in document.split("```json\n")[1:]]
    assert len(blocks) == 2
    success, failure = (json_module.loads(block) for block in blocks)
    assert success["api_version"] == API_VERSION
    assert failure["type"] == ERROR_RESPONSE_TYPE
    assert success["degradations"][0]["kind"] == "DegradationKind"
    assert failure["error"]["exit_code"] == "int"


def test_the_two_conventions_an_agent_gets_wrong_first_are_both_stated(document: str):
    assert "stdout is the machine's, stderr is the human's" in document
    assert "**`--pages` is 0-based**" in document


# ─────────────── the hand-written table cannot drift from the tree ───────────────


def test_every_exit_code_the_error_tree_produces_has_a_label_and_no_label_is_unused(
    manifest_data: dict[str, Any],
):
    produced = {int(entry["exit_code"]) for entry in manifest_data["errors"]}
    assert produced == set(EXIT_CODE_MEANINGS)


# ─────────────── determinism, so --check means drift and nothing else ───────────────


def test_rendering_twice_gives_the_same_bytes(manifest_data: dict[str, Any]):
    assert render_document(manifest_data) == render_document(manifest_data)


def test_no_default_names_the_running_user_so_the_check_survives_another_machine(document: str):
    assert str(Path.home()) not in document
    assert '`"~/.ssh/id_ed25519_remarkable"`' in document


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (str(Path.home() / ".config" / "x"), "~/.config/x"),
        (str(Path.home()), str(Path.home())),
        ("/etc/hosts", "/etc/hosts"),
        ("10.11.99.1", "10.11.99.1"),
        (64, 64),
        (None, None),
        (["textract"], ["textract"]),
    ],
)
def test_only_a_path_under_home_is_rewritten(value: object, expected: object):
    assert _home_relative(value) == expected


def test_a_pipe_is_escaped_so_a_union_type_cannot_split_a_table_row(document: str):
    assert _code("Path | None") == r"`Path \| None`"
    assert "| `Path \\| None` |" in document


def test_a_help_string_is_flattened_and_its_rst_backticks_become_markdown():
    assert _inline("one\n  two ``three``") == "one two `three`"
    assert _inline(None) == ""
    assert _inline("a | b") == r"a \| b"


# ─────────────── the two modes ───────────────


def test_writing_creates_the_file_and_reports_success(tmp_path: Path, document: str):
    target = tmp_path / "AGENTS.md"
    assert main([str(target)]) == 0
    assert target.read_text(encoding="utf-8") == document


def test_checking_a_matching_file_passes_silently(
    tmp_path: Path,
    document: str,
    capsys: pytest.CaptureFixture[str],
):
    target = tmp_path / "AGENTS.md"
    target.write_text(document, encoding="utf-8")
    assert main([str(target), "--check"]) == 0
    assert capsys.readouterr().err == ""


def test_checking_a_drifted_file_fails_and_diffs_it(
    tmp_path: Path,
    document: str,
    capsys: pytest.CaptureFixture[str],
):
    target = tmp_path / "AGENTS.md"
    target.write_text(f"{document}an edit nobody generated\n", encoding="utf-8")
    assert main([str(target), "--check"]) == 1
    captured = capsys.readouterr().err
    assert "+an edit nobody generated" not in captured
    assert "-an edit nobody generated" in captured
    assert REGENERATE_TASK in captured


def test_checking_a_missing_file_fails_rather_than_reporting_agreement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    target = tmp_path / "nowhere" / "AGENTS.md"
    assert main([str(target), "--check"]) == 1
    assert REGENERATE_TASK in capsys.readouterr().err
    assert not target.exists(), "--check must never write"
