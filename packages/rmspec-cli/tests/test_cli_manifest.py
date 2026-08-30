"""``rmspec manifest``: the introspection, its private hinge, and the under-reporting guards."""

from __future__ import annotations

import ast
import importlib
import json as json_module
import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Optional, Self

import pytest
from cyclopts import App, Parameter

from rmspec.cli import _manifest, _sync
from rmspec.cli import app as root_app
from rmspec.cli._manifest import (
    _COMMANDS_ATTRIBUTE,
    MANIFEST_RESPONSE_TYPE,
    RESPONSE_TYPES,
    SETTING_PREFIX,
    _describe_commands,
    _generic_type_name,
    _jsonable,
    _registrations,
    _type_name,
    _version,
    build_manifest,
    manifest,
    response_types,
)
from rmspec.cli._settings import CliSettings
from rmspec.domain.errors import DegradationKind, RmspecError, UsageError, exit_code

if TYPE_CHECKING:
    from types import ModuleType

_REPR_ADDRESS = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)
_CLI_SOURCE = Path(_manifest.__file__).parent


class _Source(StrEnum):
    DEVICE = "device"
    MIRROR = "mirror"


def _synthetic_app() -> App:
    """Build an app with the shapes the real one does not have yet.

    A group, a leaf with no docstring, a leaf with no output flags, a positional-only
    parameter, a positional-or-keyword one, a var-positional one, a ``Literal``, an ``Enum``
    and an alias. Every branch of the walker that the two commands registered today cannot
    reach is reachable here, and none of it needs a tablet.
    """
    built = App(name="synthetic", version="9.9.9")

    @built.command(name="ls", alias=["list", "l"])
    def ls(  # noqa: ANN202 - the walker reads the signature, not this annotation
        path: str | None = None,
        /,
        *,
        source: _Source = _Source.DEVICE,
        kind: Literal["a", "b"] = "a",
        out: Path | None = None,
        json: Annotated[bool, Parameter(name="--json", negative="")] = False,
    ):
        """List things.

        Parameters
        ----------
        path
            Where to look.
        """

    @built.command(name="quiet")
    def quiet(first: int = 0, *rest: str, tags: tuple[str, ...] = ()):  # noqa: ANN202
        """Offer no machine mode at all."""

    @built.command(name="bare")
    def bare():  # noqa: ANN202
        pass

    group = App(name="group", help="A group of things.")
    built.command(group, name="group")

    @group.command(name="inner")
    def inner():  # noqa: ANN202
        """Nested."""

    return built


def _command(described: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(entry for entry in described if entry["name"] == name)


def _parameter(command: dict[str, Any], name: str) -> dict[str, Any]:
    return next(entry for entry in command["parameters"] if entry["name"] == name)


def _resolved(node: ast.expr, module: ModuleType, /) -> str:
    """Reduce one ``response_type=`` argument to the string it evaluates to.

    Three spellings appear in this package and no others: a bare literal, a module constant
    (``LS_RESPONSE_TYPE``, ``HISTORY_RESPONSE_TYPE``), and a subscript of the shared table
    (``RESPONSE_TYPES["push"]``, ``RESPONSE_TYPES[COMMAND]``). Anything else fails loudly
    rather than being skipped, because a silently skipped call site would turn this guard into
    the thing it exists to catch.
    """
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return str(getattr(module, node.id))
    assert isinstance(node, ast.Subscript), f"unhandled response_type= expression in {module}"
    assert isinstance(node.value, ast.Name), f"unhandled response_type= table in {module}"
    return str(getattr(module, node.value.id)[_resolved(node.slice, module)])


def _emitted_response_types() -> set[str]:
    """Collect every ``type`` this package can put in a success envelope, from the source.

    Reads the ``response_type=`` keyword of every call in every module of ``rmspec.cli``,
    which is the only way a discriminator reaches ``CliOutput.emit``. A module is imported
    only when it has such a call, so ``_markdown.py`` -- whose module scope loads WeasyPrint's
    native libraries -- is never touched.
    """
    found: set[str] = set()
    for path in sorted(_CLI_SOURCE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        arguments = [
            keyword.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "response_type"
        ]
        if not arguments:
            continue
        suffix = "" if path.stem == "__init__" else f".{path.stem}"
        module = importlib.import_module(f"rmspec.cli{suffix}")
        found.update(_resolved(argument, module) for argument in arguments)
    return found


# ─────────────────── the private hinge, pinned so an upgrade fails loudly ───────────────────


def test_the_one_private_cyclopts_attribute_still_exists_and_is_an_ordered_mapping():
    commands = getattr(root_app, _COMMANDS_ATTRIBUTE)
    assert isinstance(commands, dict)
    assert commands, "App._commands is empty; the manifest would report no commands at all"
    assert "doctor" in commands


def test_the_private_route_and_the_public_one_agree_on_the_command_set():
    private = {key for key, _aliases, _child in _registrations(root_app)}
    reserved = set(root_app.help_flags) | set(root_app.version_flags)
    public = set(root_app.resolved_commands()) - reserved
    assert private == public


def test_only_one_function_in_the_package_reads_the_private_attribute():
    tree = ast.parse(Path(_manifest.__file__).read_text(encoding="utf-8"))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "_COMMANDS_ATTRIBUTE"
    ]
    assert len(reads) == 1, "the private cyclopts attribute is read in more than one place"
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert _COMMANDS_ATTRIBUTE not in attributes, "it is also reached by plain attribute access"


def test_reserved_pseudo_commands_are_not_reported_as_commands():
    names = {key for key, _aliases, _child in _registrations(root_app)}
    assert not names & (set(root_app.help_flags) | set(root_app.version_flags))


def test_aliases_are_folded_by_object_identity_not_counted_as_commands():
    described = _describe_commands(_synthetic_app(), ())
    assert _command(described, "ls")["aliases"] == ["list", "l"]
    assert [entry["name"] for entry in described].count("ls") == 1


# ──────────────────────────── commands ────────────────────────────


def _invocations(parent: App, path: tuple[str, ...] = ()) -> list[str]:
    """Every invocation the app answers, as the space-joined words a user types.

    A second, deliberately naive walk over the same tree the manifest walks. It recurses
    because `device info` is a leaf under a group, so comparing against the *root's* children
    alone would demand `device` and forbid `device info` -- which is what this assertion did
    until `device info` was registered, and it caught the mismatch rather than the real defect.
    """
    found = []
    for key, _aliases, child in _registrations(parent):
        here = (*path, key)
        if child.default_command is not None:
            found.append(" ".join(here))
        found.extend(_invocations(child, here))
    return found


def test_every_registered_command_appears_exactly_once():
    described = build_manifest(root_app)["commands"]
    names = [entry["name"] for entry in described]
    assert sorted(names) == sorted(set(names))
    expected = _invocations(root_app)
    assert sorted(expected) == sorted(set(expected))
    assert set(names) == set(expected)


def test_a_group_contributes_its_word_and_is_not_itself_a_command():
    names = {entry["name"] for entry in build_manifest(root_app)["commands"]}
    assert "device info" in names
    assert "device" not in names


def test_every_registered_command_has_help_text():
    for entry in build_manifest(root_app)["commands"]:
        assert entry["help"], f"{entry['name']} has no help text"


def test_every_registered_command_declares_at_least_one_response_type():
    for entry in build_manifest(root_app)["commands"]:
        assert entry["response_types"], (
            f"{entry['name']} is registered but has no RESPONSE_TYPES entry"
        )


def test_the_response_type_table_covers_the_whole_frozen_command_table():
    assert RESPONSE_TYPES["manifest"] == MANIFEST_RESPONSE_TYPE
    assert RESPONSE_TYPES["device info"] == "facts"
    assert len(set(RESPONSE_TYPES.values())) == len(RESPONSE_TYPES)


def test_a_command_with_one_shape_advertises_a_one_item_list():
    described = {e["name"]: e for e in build_manifest(root_app)["commands"]}
    assert described["ls"]["response_types"] == ["catalog"]


def test_sync_advertises_the_discriminator_its_flag_selects_read_from_the_module_that_owns_it():
    described = {e["name"]: e for e in build_manifest(root_app)["commands"]}
    assert described["sync"]["response_types"] == ["sync", _sync.HISTORY_RESPONSE_TYPE]
    assert described["sync"]["response_types"] == ["sync", "history"]


def test_an_invocation_the_table_does_not_know_advertises_nothing_rather_than_an_empty_list():
    assert response_types("no-such-command") is None


def test_every_response_type_any_command_can_emit_is_advertised_by_the_manifest():
    """The anti-under-report guard, in the one direction that costs a caller correctness.

    An agent branches on ``type`` *before* it looks at ``data``. A discriminator a command
    really emits but the manifest never names is a branch that agent cannot have written --
    which is exactly what ``sync --history`` was until ``response_types`` became a list. So
    this compares the two sets both ways: nothing emitted may be unadvertised, and nothing
    advertised may be unreachable.
    """
    advertised = {
        response_type
        for entry in build_manifest(root_app)["commands"]
        for response_type in entry["response_types"]
    }
    assert _emitted_response_types() == advertised


def test_a_group_contributes_a_prefix_and_is_not_itself_a_command():
    described = _describe_commands(_synthetic_app(), ())
    names = [entry["name"] for entry in described]
    assert "group inner" in names
    assert "group" not in names


def test_a_command_without_a_docstring_reports_no_help_rather_than_an_empty_string():
    assert _command(_describe_commands(_synthetic_app(), ()), "bare")["help"] is None


def test_modes_are_read_from_the_flags_a_command_actually_declares():
    described = _describe_commands(_synthetic_app(), ())
    assert _command(described, "ls")["modes"] == ["human", "json"]
    assert _command(described, "quiet")["modes"] == ["human"]


def test_the_real_commands_offer_all_three_modes():
    for entry in build_manifest(root_app)["commands"]:
        assert entry["modes"] == ["human", "json", "dense"]


# ──────────────────────────── parameters ────────────────────────────


def test_a_positional_only_parameter_reports_its_python_name_and_its_cli_spelling():
    parameter = _parameter(_command(_describe_commands(_synthetic_app(), ()), "ls"), "path")
    assert parameter["flags"] == ["PATH"]
    assert parameter["kind"] == "positional"
    assert parameter["type"] == "str"
    assert parameter["required"] is False
    assert parameter["default"] is None
    assert parameter["help"] == "Where to look."


def test_an_enum_parameter_reports_its_choices_and_a_json_ready_default():
    parameter = _parameter(_command(_describe_commands(_synthetic_app(), ()), "ls"), "source")
    assert parameter["choices"] == ["device", "mirror"]
    assert parameter["default"] == "device"
    assert parameter["kind"] == "keyword"


def test_a_literal_parameter_reports_its_choices():
    parameter = _parameter(_command(_describe_commands(_synthetic_app(), ()), "ls"), "kind")
    assert parameter["choices"] == ["a", "b"]
    assert parameter["type"] == "Literal['a', 'b']"


def test_a_path_parameter_names_the_public_class_not_the_private_module():
    parameter = _parameter(_command(_describe_commands(_synthetic_app(), ()), "ls"), "out")
    assert parameter["type"] == "Path"


def test_a_flag_is_marked_as_consuming_no_token_and_lists_no_negative():
    parameter = _parameter(_command(_describe_commands(_synthetic_app(), ()), "ls"), "json")
    assert parameter["is_flag"] is True
    assert parameter["negative_flags"] == []
    assert parameter["flags"] == ["--json"]


def test_the_other_two_parameter_kinds_are_reported():
    quiet = _command(_describe_commands(_synthetic_app(), ()), "quiet")
    assert _parameter(quiet, "first")["kind"] == "positional_or_keyword"
    assert _parameter(quiet, "rest")["kind"] == "var_positional"


def test_a_generated_negative_is_reported_rather_than_hidden():
    quiet = _command(_describe_commands(_synthetic_app(), ()), "quiet")
    tags = _parameter(quiet, "tags")
    assert tags["negative_flags"] == ["--empty-tags"]
    assert "--empty-tags" in tags["flags"]


def test_the_walker_invents_no_fifth_parameter_kind():
    described = _describe_commands(_synthetic_app(), ()) + build_manifest(root_app)["commands"]
    kinds = {p["kind"] for command in described for p in command["parameters"]}
    assert kinds <= set(_manifest._PARAMETER_KINDS.values())


# ──────────────────────────── errors ────────────────────────────


def _subclasses(cls: type[RmspecError]) -> set[type[RmspecError]]:
    found = set()
    for child in cls.__subclasses__():
        found.add(child)
        found |= _subclasses(child)
    return found


def test_every_error_class_in_the_tree_is_reported_root_included():
    reported = {entry["type"] for entry in build_manifest(root_app)["errors"]}
    expected = {cls.__name__ for cls in _subclasses(RmspecError)} | {"RmspecError"}
    assert reported == expected


def test_each_error_is_scored_by_the_domains_own_function():
    by_name = {entry["type"]: entry for entry in build_manifest(root_app)["errors"]}
    assert by_name["UsageError"]["exit_code"] == exit_code(
        UsageError(subject="x", requirement="y")
    )
    assert by_name["RmspecError"]["exit_code"] == 1


def test_an_intermediate_is_marked_abstract_and_a_leaf_is_not():
    by_name = {entry["type"]: entry for entry in build_manifest(root_app)["errors"]}
    assert by_name["DocumentSourceError"]["abstract"] is True
    assert by_name["PageNotFound"]["abstract"] is False


def test_every_exit_code_in_the_domains_table_is_visible_in_the_manifest():
    codes = {entry["exit_code"] for entry in build_manifest(root_app)["errors"]}
    assert codes == {1, 2, 65, 66, 69, 70, 73, 74, 75, 77, 78}


# ──────────────────── degradation kinds and settings ────────────────────


def test_the_closed_degradation_set_is_reported_in_declaration_order():
    assert build_manifest(root_app)["degradation_kinds"] == [k.value for k in DegradationKind]


def test_every_setting_is_reported_with_its_prefixed_variable_and_its_own_docstring():
    described = build_manifest(root_app)["settings"]
    assert [entry["field"] for entry in described] == list(CliSettings.model_fields)
    for entry in described:
        assert entry["name"] == f"{SETTING_PREFIX}{entry['field'].upper()}"
        assert entry["help"], f"{entry['name']} has no docstring"


def test_the_manifest_prefix_agrees_with_the_settings_model():
    assert CliSettings.model_config["env_prefix"] == SETTING_PREFIX


def test_a_default_factory_is_resolved_rather_than_serialised_as_a_function():
    described = {entry["name"]: entry for entry in build_manifest(root_app)["settings"]}
    assert described["RMSPEC_SSH_KEY"]["default"].endswith("id_ed25519_remarkable")
    assert described["RMSPEC_MAX_PAGES"]["default"] == 64
    assert described["RMSPEC_OCR_ENGINES"]["default"] == ["textract"]


def test_a_setting_default_is_read_from_the_declaration_and_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "9")
    described = {entry["name"]: entry for entry in build_manifest(root_app)["settings"]}
    assert described["RMSPEC_MAX_PAGES"]["default"] == 64


# ──────────────────────────── the renderers ────────────────────────────


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (str, "str"),
        (None, "None"),
        (type(None), "None"),
        (..., "..."),
        (Path | None, "Path | None"),
        (Optional[int], "int | None"),  # noqa: UP045 - the other union spelling, on purpose
        (tuple[str, ...], "tuple[str, ...]"),
        (frozenset[_Source], "frozenset[_Source]"),
        (Literal["a", 1], "Literal['a', 1]"),
        (Any, "Any"),
        (Self, "Self"),
        ("AForwardReference", "AForwardReference"),
    ],
)
def test_a_type_is_named_the_way_a_reader_would_write_it(hint: object, expected: str):
    assert _type_name(hint) == expected


def test_a_parameterised_type_is_rebuilt_from_its_origin():
    assert _generic_type_name(frozenset, (str,)) == "frozenset[str]"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_Source.MIRROR, "mirror"),
        (Path("/tmp/x"), "/tmp/x"),  # noqa: S108 - a literal, never opened
        (frozenset({_Source.MIRROR, _Source.DEVICE}), ["device", "mirror"]),
        ((1, 2), [1, 2]),
        ([1, 2], [1, 2]),
        (None, None),
        (True, True),
        (3, 3),
        (1.5, 1.5),
        ("x", "x"),
        (DegradationKind, str(DegradationKind)),
    ],
)
def test_every_introspected_value_reduces_to_something_json_can_write(
    value: object,
    expected: object,
):
    assert _jsonable(value) == expected


def test_a_declared_version_string_is_passed_through_and_a_callable_is_called():
    assert _version(_synthetic_app()) == "9.9.9"
    assert _version(root_app) == build_manifest(root_app)["version"]


def test_no_value_anywhere_in_the_manifest_is_a_python_repr():
    document = json_module.dumps(build_manifest(root_app))
    assert not _REPR_ADDRESS.search(document), "something was serialised by repr, not by value"
    assert "<function" not in document
    assert "<class" not in document


# ──────────────────────────── the command ────────────────────────────


def test_the_command_emits_the_manifest_in_the_same_envelope_as_everything_else(
    capsys: pytest.CaptureFixture[str],
):
    assert manifest(json=True) == 0
    document = json_module.loads(capsys.readouterr().out)
    assert document["api_version"] == "rmspec/v1"
    assert document["type"] == MANIFEST_RESPONSE_TYPE
    assert document["degradations"] == []
    assert document["data"]["name"] == "rmspec"


def test_the_command_emits_the_envelope_by_default_because_a_manifest_has_no_table(
    capsys: pytest.CaptureFixture[str],
):
    assert manifest() == 0
    assert json_module.loads(capsys.readouterr().out)["type"] == MANIFEST_RESPONSE_TYPE


def test_dense_projects_one_record_per_command(capsys: pytest.CaptureFixture[str]):
    assert manifest(dense=True) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t") == ["command", "response_types", "help"]
    assert len(lines) == 1 + len(build_manifest(root_app)["commands"])
    assert all(len(line.split("\t")) == 3 for line in lines)
    assert any(line.startswith("sync\tsync,history\t") for line in lines)


def test_two_output_modes_are_refused_by_the_same_boundary_as_every_command(
    capsys: pytest.CaptureFixture[str],
):
    assert manifest(json=True, dense=True) == 2
    assert json_module.loads(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_the_command_reads_no_setting_so_a_broken_environment_cannot_hide_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("RMSPEC_XOCHITLE", "/nowhere")
    assert manifest(json=True) == 0
    assert json_module.loads(capsys.readouterr().out)["type"] == MANIFEST_RESPONSE_TYPE
