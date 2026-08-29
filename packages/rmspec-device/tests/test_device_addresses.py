"""Addresses are data, and an unquoted shell interpolation is unrepresentable.

Two properties carry this file. The first is that no value reaching
``RemoteCommand.of`` can escape its shell word -- asserted over arbitrary text with
hypothesis rather than over a list of the metacharacters somebody thought of, because the
legacy hole at ``src/remarkable_spec/device/sync.py`` lines 226, 530 and 532 was written
by someone who had thought about it. The second is that ``RemotePath.child`` either
refuses a name or returns a path strictly inside its parent, so a uuid from the wire is
safe to use as a path component without the caller inspecting it.

The rest is the boring half that matters just as much: these are the names the firmware
chose, and a typo in one of them is a document that silently does not exist.
"""

from __future__ import annotations

import posixpath
import shlex

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from rmspec.device.addresses import (
    CONTENT_SUFFIX,
    DEFAULT_USB_HOST,
    METADATA_SUFFIX,
    OS_RELEASE,
    PROC_MEMINFO,
    SCENE_SUFFIX,
    SOC_MACHINE,
    SSH_PORT,
    WEB_API_PORT,
    XOCHITL_ROOT,
    DocumentPaths,
    Endpoint,
    RemoteCommand,
    RemotePath,
    document_paths,
)

DOC = "b8ff2c3d-0a1e-4f77-9c21-6a0e5d4b7f10"
PAGE = "1f0a9c72-3d44-4e18-8b56-2c7d9e0a5b31"
PARENT = "/home/root/.local/share/remarkable/xochitl"


def _assert_frozen(model: BaseModel, field: str, value: object) -> None:
    """Assert a frozen model refuses field assignment.

    Goes through ``setattr`` rather than a direct assignment because ``ty`` runs strict
    over this suite and resolves ``model.field = value`` on a frozen model to
    ``invalid-assignment`` statically -- so the direct form fails the type gate even though
    it is exactly the runtime behaviour the test exists to pin. The same helper is used by
    ``packages/rmspec-domain/tests/test_ports_export.py`` for the same reason.
    """
    with pytest.raises(ValidationError):
        setattr(model, field, value)


def _assert_rejects(model: type[BaseModel], members: dict[str, object]) -> None:
    """Assert a model refuses these members.

    Goes through ``model_validate`` rather than ``Model(**members)`` for the same reason:
    ``ty`` checks the unpacked call against the real field types and reports an unknown
    keyword or a widened value type, which is the very thing being asserted at runtime.
    """
    with pytest.raises(ValidationError):
        model.model_validate(members)


# ─────────────────────────── the constants ───────────────────────────


def test_the_measured_device_constants_are_spelled_exactly_once_and_correctly():
    # Every one of these was read off firmware 3.27.3.0. A typo here is a request to
    # the wrong place or a file that does not exist, and nothing else in the workspace
    # spells them, so this is the only assertion guarding them.
    assert DEFAULT_USB_HOST == "10.11.99.1"
    assert WEB_API_PORT == 80
    assert SSH_PORT == 22
    assert XOCHITL_ROOT == "/home/root/.local/share/remarkable/xochitl"
    assert OS_RELEASE == "/etc/os-release"
    assert SOC_MACHINE == "/sys/devices/soc0/machine"
    assert PROC_MEMINFO == "/proc/meminfo"
    assert (METADATA_SUFFIX, CONTENT_SUFFIX, SCENE_SUFFIX) == (".metadata", ".content", ".rm")


def test_the_firmware_version_source_is_not_the_path_that_does_not_exist():
    """Legacy ``DevicePaths.UPDATE_CONF`` named a file measured absent on 3.27.3.0."""
    assert OS_RELEASE != "/usr/share/remarkable/update.conf"


# ─────────────────────────── Endpoint ───────────────────────────


def test_the_default_endpoint_is_the_attached_tablet_over_usb():
    endpoint = Endpoint()

    assert endpoint.host == DEFAULT_USB_HOST
    assert endpoint.port == WEB_API_PORT
    assert endpoint.base_url == "http://10.11.99.1"


def test_a_non_default_port_appears_in_the_url():
    # A user forwarding the web API through a local tunnel is still reaching a tablet.
    assert Endpoint(host="127.0.0.1", port=8080).base_url == "http://127.0.0.1:8080"


def test_an_endpoint_built_for_ssh_keeps_its_port():
    assert Endpoint(port=SSH_PORT).port == SSH_PORT


@pytest.mark.parametrize("field", [{"host": ""}, {"port": 0}, {"port": 65536}, {"nope": 1}])
def test_an_endpoint_refuses_a_value_it_could_not_connect_with(field: dict[str, object]):
    _assert_rejects(Endpoint, field)


def test_an_endpoint_is_frozen():
    _assert_frozen(Endpoint(), "host", "other")


# ─────────────────────────── RemotePath construction ───────────────────────────


def test_the_root_is_the_xochitl_data_directory():
    assert RemotePath.root().value == XOCHITL_ROOT


def test_a_literal_path_this_package_knows_becomes_a_remote_path():
    assert RemotePath.absolute(OS_RELEASE).value == OS_RELEASE


def test_the_filesystem_root_is_a_valid_path():
    # "/" is the one value that ends in a slash and is still one plain absolute path.
    assert RemotePath.absolute("/").value == "/"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("etc/os-release", id="relative"),
        pytest.param("./etc", id="dot-relative"),
        pytest.param("/etc/", id="trailing-slash"),
        pytest.param("/etc//os-release", id="empty-component"),
        pytest.param("/etc/./os-release", id="dot-component"),
        pytest.param("/home/root/../etc/passwd", id="parent-component"),
        pytest.param("/etc/os\0release", id="nul-byte"),
    ],
)
def test_a_path_that_is_not_one_plain_absolute_path_is_refused(value: str):
    # Normalising instead would accept the traversal cases by resolving them, which
    # would put child()'s containment guarantee on top of a parent that had already
    # escaped.
    with pytest.raises(ValueError, match=r".*"):
        RemotePath.absolute(value)


def test_a_path_is_frozen_and_forbids_extra_fields():
    _assert_frozen(RemotePath.root(), "value", "/mnt/other")
    _assert_rejects(RemotePath, {"value": "/etc", "extra": "x"})


def test_a_path_does_not_interpolate_as_the_bare_path():
    """The whole point: an f-string cannot produce the dangerous form by accident."""
    path = RemotePath.root()

    assert str(path) != path.value
    assert f"{path}" != path.value
    assert path.value in str(path)  # visible in a message, just not interpolable


def test_the_only_interpolable_accessor_yields_the_quoted_form():
    assert RemotePath.absolute("/mnt/a b").quoted == "'/mnt/a b'"
    assert RemotePath.absolute("/etc/os-release").quoted == "/etc/os-release"


# ─────────────────────────── child ───────────────────────────


def test_a_child_appends_one_component():
    assert RemotePath.root().child(DOC).value == f"{XOCHITL_ROOT}/{DOC}"


def test_a_child_of_the_filesystem_root_does_not_double_the_separator():
    assert RemotePath.absolute("/").child("etc").value == "/etc"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("/", id="separator"),
        pytest.param("a/b", id="two-components"),
        pytest.param("..", id="parent"),
        pytest.param(".", id="self"),
        pytest.param("-rf", id="leading-dash"),
        pytest.param("--force", id="leading-dashes"),
    ],
)
def test_a_component_that_could_change_the_command_is_refused(name: str):
    with pytest.raises(ValueError, match=r".*"):
        RemotePath.root().child(name)


def test_a_leading_dash_is_refused_because_quoting_does_not_help():
    # shlex.quote("-rf") is "-rf": the argument is already one shell word, and BusyBox
    # would still read it as an option. Refusing the name is the only fix.
    assert shlex.quote("-rf") == "-rf"
    with pytest.raises(ValueError, match="option"):
        RemotePath.root().child("-rf")


@given(name=st.text())
def test_a_child_is_either_refused_or_strictly_inside_its_parent(name: str):
    parent = RemotePath.absolute(PARENT)
    try:
        child = parent.child(name)
    except ValueError:
        return  # refusal is the other half of the guarantee

    assert child.value.startswith(f"{parent.value}/")
    assert posixpath.dirname(child.value) == parent.value
    assert posixpath.normpath(child.value).startswith(f"{parent.value}/")


# ─────────────────────────── with_suffix ───────────────────────────


def test_a_suffix_is_appended_to_the_last_component():
    assert RemotePath.root().child(DOC).with_suffix(METADATA_SUFFIX).value == (
        f"{XOCHITL_ROOT}/{DOC}{METADATA_SUFFIX}"
    )


@given(name=st.text(alphabet="abc.-0", min_size=1, max_size=8))
def test_a_dotted_identifier_keeps_its_whole_name(name: str):
    """``pathlib.Path.with_suffix`` would eat the tail; see ``rmspec.formats.layout``."""
    assume(not name.startswith("-"))
    assume(name not in {".", ".."})
    parent = RemotePath.absolute(PARENT)

    assert parent.child(name).with_suffix(CONTENT_SUFFIX).value == (
        f"{PARENT}/{name}{CONTENT_SUFFIX}"
    )


@pytest.mark.parametrize("suffix", [pytest.param("", id="empty"), pytest.param("a/b", id="slash")])
def test_a_suffix_that_would_name_another_file_is_refused(suffix: str):
    with pytest.raises(ValueError, match=r".*"):
        RemotePath.root().with_suffix(suffix)


# ─────────────────────────── RemoteCommand ───────────────────────────


def test_a_command_with_no_placeholders_is_its_template():
    assert RemoteCommand.of("systemctl restart xochitl").text == "systemctl restart xochitl"


def test_a_path_argument_is_substituted_quoted():
    command = RemoteCommand.of("ls -A {}", RemotePath.root())

    assert command.text == f"ls -A {XOCHITL_ROOT}"
    assert shlex.split(command.text) == ["ls", "-A", XOCHITL_ROOT]


def test_a_string_argument_is_substituted_quoted():
    assert RemoteCommand.of("df -Pk {}", "/mnt/a b").text == "df -Pk '/mnt/a b'"


def test_several_arguments_fill_the_placeholders_in_order():
    command = RemoteCommand.of("cp {} {}", "one", "two")

    assert command.text == "cp one two"


def test_a_template_keeps_the_operators_it_carries():
    # The BusyBox firmware command from the design's table, whose sed script contains
    # backslashes and parentheses that must survive untouched.
    template = "sed -n 's/^IMG_VERSION=\"\\(.*\\)\"$/\\1/p' {}"
    command = RemoteCommand.of(template, RemotePath.absolute(OS_RELEASE))

    assert command.text == f"sed -n 's/^IMG_VERSION=\"\\(.*\\)\"$/\\1/p' {OS_RELEASE}"


@pytest.mark.parametrize(
    ("template", "args"),
    [
        pytest.param("ls {}", (), id="too-few"),
        pytest.param("ls {}", ("a", "b"), id="too-many"),
        pytest.param("ls", ("a",), id="none-wanted"),
        pytest.param("cp {} {}", ("a",), id="one-short"),
    ],
)
def test_a_placeholder_count_that_disagrees_with_the_arguments_is_refused(
    template: str,
    args: tuple[str, ...],
):
    # Otherwise a caller silently ships a literal "{}" to the device, or drops an
    # argument and runs a command against the wrong thing.
    with pytest.raises(ValueError, match="placeholder"):
        RemoteCommand.of(template, *args)


def test_an_empty_command_is_refused():
    with pytest.raises(ValidationError):
        RemoteCommand.of("")


def test_a_command_is_frozen():
    _assert_frozen(RemoteCommand.of("ls"), "text", "rm -rf /")


@given(args=st.lists(st.text(), min_size=1, max_size=4))
def test_no_argument_can_escape_its_shell_word(args: list[str]):
    """The legacy hole, made unrepresentable rather than discouraged.

    ``shlex.split`` is the oracle: if the command parses back to exactly the arguments
    that went in, then no argument contributed an operator, a word break, a quote or a
    comment to the command -- whatever bytes it contained.
    """
    template = "cmd" + " {}" * len(args)

    command = RemoteCommand.of(template, *args)

    assert shlex.split(command.text) == ["cmd", *args]


@given(name=st.text(min_size=1))
def test_a_path_argument_reaches_the_device_as_exactly_one_word(name: str):
    parent = RemotePath.absolute(PARENT)
    try:
        path = parent.child(name)
    except ValueError:
        return

    command = RemoteCommand.of("mkdir -p {}", path)

    assert shlex.split(command.text) == ["mkdir", "-p", path.value]


# ─────────────────────────── document paths ───────────────────────────


def test_a_documents_artifacts_are_named_by_appending_their_suffixes():
    paths = document_paths(RemotePath.root(), DOC)

    assert paths.metadata.value == f"{XOCHITL_ROOT}/{DOC}.metadata"
    assert paths.content.value == f"{XOCHITL_ROOT}/{DOC}.content"
    assert paths.page_dir.value == f"{XOCHITL_ROOT}/{DOC}"


def test_document_paths_can_be_built_under_any_root():
    paths = document_paths(RemotePath.absolute("/mnt/mirror"), DOC)

    assert paths.metadata.value == f"/mnt/mirror/{DOC}.metadata"


@pytest.mark.parametrize("uuid", ["", "..", "a/b", "-rf"])
def test_a_uuid_that_is_not_one_component_never_becomes_a_path(uuid: str):
    with pytest.raises(ValueError, match=r".*"):
        document_paths(RemotePath.root(), uuid)


def test_a_page_artifact_lives_in_the_document_directory():
    paths = document_paths(RemotePath.root(), DOC)

    assert paths.page(PAGE).value == f"{XOCHITL_ROOT}/{DOC}/{PAGE}.rm"


@pytest.mark.parametrize("page_id", ["", "..", "a/b", "-rf"])
def test_a_page_id_that_is_not_one_component_is_refused(page_id: str):
    paths = document_paths(RemotePath.root(), DOC)
    with pytest.raises(ValueError, match=r".*"):
        paths.page(page_id)


@pytest.mark.parametrize("suffix", ["pdf", "epub"])
def test_an_underlay_sits_beside_the_sidecars(suffix: str):
    paths = document_paths(RemotePath.root(), DOC)

    assert paths.underlay(suffix).value == f"{XOCHITL_ROOT}/{DOC}.{suffix}"


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param("", id="empty"),
        pytest.param(".pdf", id="dotted"),
        pytest.param("p/df", id="separator"),
    ],
)
def test_an_underlay_type_is_the_bare_type_as_content_spells_it(suffix: str):
    # No normalisation: accepting both "pdf" and ".pdf" would hide a caller that had
    # confused the two, and one of those callers would eventually pass "..pdf".
    paths = document_paths(RemotePath.root(), DOC)
    with pytest.raises(ValueError, match=r".*"):
        paths.underlay(suffix)


def test_document_paths_is_frozen_and_forbids_extra_fields():
    paths = document_paths(RemotePath.root(), DOC)
    _assert_frozen(paths, "metadata", RemotePath.root())
    _assert_rejects(
        DocumentPaths,
        {
            "metadata": paths.metadata,
            "content": paths.content,
            "page_dir": paths.page_dir,
            "pagedata": paths.content,
        },
    )
