"""``rmspec ls``: one query, three renderings, a subtree filter, and orphans nobody may drop."""

from __future__ import annotations

import json as json_module
import os
from typing import TYPE_CHECKING, Any

import pytest
from cyclopts import App
from dishka import Provider, Scope, provide

from rmspec.cli import _ls
from rmspec.cli._invoke import Invoked
from rmspec.cli._invoke import run as _real_run
from rmspec.cli._ls import (
    ABSENT_CELL,
    DENSE_COLUMNS,
    DOCUMENT_KIND,
    FALSE_CELL,
    FOLDER_KIND,
    LS_RESPONSE_TYPE,
    PATH_SEPARATOR,
    TRANSPORT_VARIABLE,
    TRUE_CELL,
    Source,
    _segments,
    ls,
)
from rmspec.cli._manifest import RESPONSE_TYPES, _describe_commands
from rmspec.cli._settings import (
    HOMEBREW_LIBRARY_DIR,
    NATIVE_LIBRARY_PATH_VAR,
    CliSettings,
    Transport,
)
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import TransportKind
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DeviceFolder,
    SkippedEntry,
    SkipReason,
)

if TYPE_CHECKING:
    from collections.abc import Callable


_MISSING_PARENT = "fld-missing"

_ROOT_DOCUMENT = DeviceDocument(
    uuid="doc-root",
    name="Root notes",
    file_type=DeviceFileType.NOTEBOOK,
    page_count=2,
)
_WORK = DeviceFolder(uuid="fld-work", name="Work")
_WORK_DOCUMENT = DeviceDocument(
    uuid="doc-work",
    name="Meeting notes",
    file_type=DeviceFileType.NOTEBOOK,
    parent_uuid=_WORK.uuid,
    page_count=5,
)
_PROJECTS = DeviceFolder(uuid="fld-projects", name="Projects", parent_uuid=_WORK.uuid)
_PROJECT_DOCUMENT = DeviceDocument(
    uuid="doc-design",
    name="Design [draft]",
    file_type=DeviceFileType.PDF,
    parent_uuid=_PROJECTS.uuid,
)
"""The one document with no page count, so ``_pages`` is measured both ways."""

_GHOST = DeviceFolder(uuid="fld-ghost", name="Ghost", parent_uuid=_MISSING_PARENT)
_LOST_DOCUMENT = DeviceDocument(
    uuid="doc-lost",
    name="Lost notes",
    file_type=DeviceFileType.NOTEBOOK,
    parent_uuid=_MISSING_PARENT,
    page_count=1,
)
_TRASHED_DOCUMENT = DeviceDocument(
    uuid="doc-trashed",
    name="Deleted notes",
    file_type=DeviceFileType.NOTEBOOK,
    page_count=1,
    trashed=True,
)

_DOCUMENTS = (
    _ROOT_DOCUMENT,
    _WORK_DOCUMENT,
    _PROJECT_DOCUMENT,
    _LOST_DOCUMENT,
    _TRASHED_DOCUMENT,
)
_FOLDERS = (_WORK, _PROJECTS, _GHOST)
_SKIPPED = (
    SkippedEntry(uuid="doc-broken", reason=SkipReason.MALFORMED_METADATA, detail="no metadata"),
)


class _CatalogDouble(Provider):
    """Bind the shipped in-memory catalog over the real device binding.

    ``override=True`` on the one port :class:`~rmspec.app.ListDocuments` reaches through is what
    keeps these tests off the wire: nothing in the resulting graph opens an SSH session or
    constructs a ``bedrock-runtime`` or ``textract`` client.
    """

    scope = Scope.REQUEST

    def __init__(self, catalog: InMemoryDeviceCatalog) -> None:
        super().__init__()
        self._catalog = catalog

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        return self._catalog


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test independent of the developer's shell, and of every other test.

    ``RMSPEC_TRANSPORT`` is *set* rather than merely deleted because ``--source`` writes it:
    monkeypatch restores a key it has touched, so pinning it here is what stops one test's
    ``--source mirror`` leaking into the next one under ``pytest-randomly``.
    """
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(TRANSPORT_VARIABLE, Transport.USB.value)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    /,
    *,
    documents: tuple[DeviceDocument, ...] = _DOCUMENTS,
    folders: tuple[DeviceFolder, ...] = _FOLDERS,
    skipped: tuple[SkippedEntry, ...] = (),
) -> None:
    """Wrap the command module's ``run`` so the in-memory catalog is bound for this call.

    ``providers=`` is ``run``'s test-only hook and a real command never passes it, so a test
    reaches it by rebinding the name the command calls. Patching the module attribute rather
    than the function it came from keeps the substitution scoped to this one command module.
    """
    catalog = InMemoryDeviceCatalog(documents=documents, folders=folders, skipped=skipped)

    def patched(
        body: Callable[[Invoked], int],
        /,
        *,
        json: bool = False,
        dense: bool = False,
    ) -> int:
        return _real_run(body, json=json, dense=dense, providers=[_CatalogDouble(catalog)])

    monkeypatch.setattr(_ls, "run", patched)


def _document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json_module.loads(capsys.readouterr().out)


def _records(capsys: pytest.CaptureFixture[str]) -> list[list[str]]:
    return [line.split("\t") for line in capsys.readouterr().out.splitlines()]


def _cells(records: list[list[str]], uuid: str) -> list[str]:
    return next(record for record in records if record[1] == uuid)


# ──────────────────────────── the JSON envelope ────────────────────────────


def test_the_envelope_carries_the_whole_catalog_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True) == 0
    document = _document(capsys)
    assert document["api_version"] == "rmspec/v1"
    assert document["type"] == LS_RESPONSE_TYPE == RESPONSE_TYPES["ls"]
    data = document["data"]
    assert [entry["uuid"] for entry in data["documents"]] == [
        _ROOT_DOCUMENT.uuid,
        _WORK_DOCUMENT.uuid,
        _PROJECT_DOCUMENT.uuid,
        _LOST_DOCUMENT.uuid,
    ]
    assert [entry["uuid"] for entry in data["root_documents"]] == [_ROOT_DOCUMENT.uuid]


def test_the_envelope_carries_the_nested_hierarchy_as_well_as_the_flat_view(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True) == 0
    data = _document(capsys)["data"]
    work = data["root_folders"][0]
    assert work["folder"]["uuid"] == _WORK.uuid
    assert [entry["uuid"] for entry in work["documents"]] == [_WORK_DOCUMENT.uuid]
    assert work["folders"][0]["folder"]["uuid"] == _PROJECTS.uuid


def test_tree_changes_no_key_of_the_payload_an_agent_parses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True) == 0
    flat = _document(capsys)
    _bind(monkeypatch)
    assert ls(tree=True, json=True) == 0
    assert _document(capsys) == flat


def test_the_orphans_are_in_the_payload_rather_than_dropped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True) == 0
    data = _document(capsys)["data"]
    assert [entry["uuid"] for entry in data["unrooted_folders"]] == [_GHOST.uuid]
    assert [entry["uuid"] for entry in data["unrooted_documents"]] == [_LOST_DOCUMENT.uuid]


def test_degradations_are_hoisted_out_of_the_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, skipped=_SKIPPED)
    assert ls(json=True) == 0
    document = _document(capsys)
    assert document["degradations"][0]["kind"] == "catalog_entry_skipped"


def test_the_trash_is_excluded_by_default_and_reported_when_asked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True) == 0
    data = _document(capsys)["data"]
    assert _TRASHED_DOCUMENT.uuid not in {entry["uuid"] for entry in data["documents"]}
    _bind(monkeypatch)
    assert ls(include_trashed=True, json=True) == 0
    data = _document(capsys)["data"]
    assert _TRASHED_DOCUMENT.uuid in {entry["uuid"] for entry in data["documents"]}


# ──────────────────────────── the dense projection ────────────────────────────


def test_dense_writes_the_documented_header_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(dense=True) == 0
    assert _records(capsys)[0] == list(DENSE_COLUMNS)


def test_dense_writes_one_record_per_entry_documents_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(dense=True) == 0
    records = _records(capsys)[1:]
    assert len(records) == 4 + len(_FOLDERS)
    assert [record[0] for record in records[:4]] == [DOCUMENT_KIND] * 4
    assert [record[0] for record in records[4:]] == [FOLDER_KIND] * len(_FOLDERS)
    assert all(len(record) == len(DENSE_COLUMNS) for record in records)


def test_dense_marks_an_entry_the_hierarchy_could_not_place(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(dense=True) == 0
    records = _records(capsys)
    assert _cells(records, _LOST_DOCUMENT.uuid)[-1] == TRUE_CELL
    assert _cells(records, _GHOST.uuid)[-1] == TRUE_CELL
    assert _cells(records, _WORK_DOCUMENT.uuid)[-1] == FALSE_CELL
    assert _cells(records, _PROJECTS.uuid)[-1] == FALSE_CELL


def test_dense_leaves_an_unreported_page_count_and_a_root_parent_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(dense=True) == 0
    records = _records(capsys)
    assert _cells(records, _PROJECT_DOCUMENT.uuid)[3] == ABSENT_CELL
    assert _cells(records, _ROOT_DOCUMENT.uuid)[3] == "2"
    assert _cells(records, _ROOT_DOCUMENT.uuid)[4] == ABSENT_CELL
    assert _cells(records, _WORK_DOCUMENT.uuid)[4] == _WORK.uuid
    assert _cells(records, _WORK.uuid)[3] == ABSENT_CELL


def test_dense_emits_the_same_records_whether_or_not_tree_was_passed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(dense=True) == 0
    flat = _records(capsys)
    _bind(monkeypatch)
    assert ls(tree=True, dense=True) == 0
    assert _records(capsys) == flat


def test_dense_keeps_its_record_stream_clean_and_warns_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, skipped=_SKIPPED)
    assert ls(dense=True) == 0
    captured = capsys.readouterr()
    assert "catalog_entry_skipped" in captured.err
    assert "catalog_entry_skipped" not in captured.out


# ──────────────────────────── the human renderings ────────────────────────────


def test_a_human_run_puts_its_table_on_stderr_and_nothing_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Meeting notes" in captured.err


def test_a_human_table_keeps_a_bracketed_name_intact(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls() == 0
    assert "[draft]" in capsys.readouterr().err


def test_the_tree_indents_the_hierarchy_and_marks_folders(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(tree=True) == 0
    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("Root notes (2p)")
    assert lines[1] == f"{_WORK.name}{PATH_SEPARATOR}"
    assert lines[2].startswith("  Meeting notes (5p)")
    assert lines[3] == f"  {_PROJECTS.name}{PATH_SEPARATOR}"
    assert lines[4].startswith("    Design [draft]  ")


def test_the_human_run_names_every_entry_the_hierarchy_could_not_place(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(tree=True) == 0
    stderr = capsys.readouterr().err
    assert f"unrooted folder: {_GHOST.name}" in stderr
    assert f"unrooted document: {_LOST_DOCUMENT.name}" in stderr


def test_a_run_with_no_orphans_says_nothing_about_them(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, documents=(_ROOT_DOCUMENT,), folders=())
    assert ls(tree=True) == 0
    assert "unrooted" not in capsys.readouterr().err


# ──────────────────────────── the PATH filter ────────────────────────────


def test_a_path_narrows_the_listing_to_one_subtree(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("Work", json=True) == 0
    data = _document(capsys)["data"]
    assert [entry["uuid"] for entry in data["documents"]] == [
        _WORK_DOCUMENT.uuid,
        _PROJECT_DOCUMENT.uuid,
    ]
    assert [entry["uuid"] for entry in data["root_documents"]] == [_WORK_DOCUMENT.uuid]
    assert data["root_folders"][0]["folder"]["uuid"] == _WORK.uuid


def test_a_path_matches_a_folder_name_case_insensitively_and_tolerates_separators(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("/WORK/", json=True) == 0
    data = _document(capsys)["data"]
    assert data["root_folders"][0]["folder"]["uuid"] == _WORK.uuid


def test_a_path_descends_more_than_one_level(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("Work/Projects", json=True) == 0
    data = _document(capsys)["data"]
    assert [entry["uuid"] for entry in data["documents"]] == [_PROJECT_DOCUMENT.uuid]
    assert data["root_folders"][0]["folder"]["uuid"] == _PROJECTS.uuid


def test_a_scoped_listing_claims_no_orphans_because_a_subtree_has_none(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("Work", json=True) == 0
    data = _document(capsys)["data"]
    assert data["unrooted_folders"] == []
    assert data["unrooted_documents"] == []


def test_a_scoped_listing_still_reports_the_runs_degradations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, skipped=_SKIPPED)
    assert ls("Work", json=True) == 0
    assert _document(capsys)["degradations"] != []


def test_a_path_naming_no_root_folder_is_refused_rather_than_answered_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("Nowhere", json=True) == 2
    error = _document(capsys)["error"]
    assert error["type"] == "UsageError"
    assert "'Nowhere' at the library root" in error["message"]


def test_a_path_naming_no_nested_folder_says_where_it_looked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls("Work/Nowhere", json=True) == 2
    error = _document(capsys)["error"]
    assert "'Nowhere' inside 'Work'" in error["message"]


def test_a_path_of_only_separators_is_refused_as_a_typo(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(" / ", json=True) == 2
    error = _document(capsys)["error"]
    assert "at least one folder name" in error["message"]


def test_a_path_is_split_on_the_separator_and_empty_segments_are_dropped():
    assert _segments("/a//b/ c /") == ("a", "b", "c")
    assert _segments("  ") == ()


# ──────────────────────────── the source flag ────────────────────────────


def test_no_source_flag_leaves_a_configured_transport_alone(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv(TRANSPORT_VARIABLE, Transport.SSH.value)
    _bind(monkeypatch)
    assert ls(json=True) == 0
    assert os.environ[TRANSPORT_VARIABLE] == Transport.SSH.value
    assert _document(capsys)["type"] == LS_RESPONSE_TYPE


def test_source_device_selects_the_usb_read_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv(TRANSPORT_VARIABLE, Transport.SSH.value)
    _bind(monkeypatch)
    assert ls(source=Source.DEVICE, json=True) == 0
    assert os.environ[TRANSPORT_VARIABLE] == Transport.USB.value
    assert _document(capsys)["type"] == LS_RESPONSE_TYPE


def test_source_mirror_refuses_in_the_domains_words_rather_than_reading_the_tablet(
    capsys: pytest.CaptureFixture[str],
):
    assert ls(source=Source.MIRROR, json=True) != 0
    error = _document(capsys)["error"]
    assert error["type"] == "DeviceOperationUnsupported"
    assert TransportKind.LOCAL_MIRROR.value in error["message"]
    assert error["remediation"] == (
        f"retry with {TransportKind.USB_WEB_API.value}, {TransportKind.SSH.value}"
    )


def test_source_mirror_is_what_the_environment_ends_up_saying():
    assert ls(source=Source.MIRROR) != 0
    assert os.environ[TRANSPORT_VARIABLE] == Transport.MIRROR.value


# ──────────────────────────── the declared surface ────────────────────────────


def test_two_output_modes_are_refused_by_the_shared_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert ls(json=True, dense=True) == 2
    error = _document(capsys)["error"]
    assert error["type"] == "UsageError"


def test_the_overridden_variable_names_a_field_the_settings_model_reads():
    assert _ls._TRANSPORT_FIELD in CliSettings.model_fields
    assert TRANSPORT_VARIABLE == "RMSPEC_TRANSPORT"


def test_every_source_names_a_transport():
    assert set(_ls._SOURCE_TRANSPORTS) == set(Source)


def test_the_manifest_can_describe_the_command_the_orchestrator_will_register():
    built = App(name="probe")
    built.command(ls, name="ls")
    described = _describe_commands(built, ())[0]
    assert described["name"] == "ls"
    assert described["help"] == "List the documents and folders the tablet holds."
    assert described["response_types"] == [LS_RESPONSE_TYPE]
    assert described["modes"] == ["human", "json", "dense"]
    parameters = {entry["name"]: entry for entry in described["parameters"]}
    assert parameters["path"]["flags"] == ["PATH"]
    assert parameters["path"]["kind"] == "positional"
    assert parameters["path"]["required"] is False
    assert parameters["source"]["choices"] == [Source.DEVICE.value, Source.MIRROR.value]
    assert parameters["tree"]["negative_flags"] == []
    assert parameters["include_trashed"]["flags"] == ["--include-trashed"]
    assert all(entry["help"] for entry in described["parameters"])
