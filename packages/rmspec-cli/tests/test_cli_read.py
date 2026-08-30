"""``rmspec read``: one selector, one document, and the ambiguity it carried either way."""

from __future__ import annotations

import datetime
import json as json_module
import os
from typing import TYPE_CHECKING, Any

import pytest
from cyclopts import App
from dishka import Provider, Scope, provide

from rmspec.cli import _read
from rmspec.cli._invoke import Invoked
from rmspec.cli._invoke import run as _real_run
from rmspec.cli._manifest import RESPONSE_TYPES, _describe_commands
from rmspec.cli._read import (
    ABSENT_CELL,
    CHOSEN_CELL,
    DENSE_COLUMNS,
    OTHER_CELL,
    READ_RESPONSE_TYPE,
    read,
)
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import AmbiguousDocument, exit_code
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    SkippedEntry,
    SkipReason,
)

if TYPE_CHECKING:
    from collections.abc import Callable


_MODIFIED = datetime.datetime(2026, 8, 29, 12, 30, tzinfo=datetime.UTC)

_FIRST = DeviceDocument(
    uuid="a" * 36,
    name="Notes one",
    file_type=DeviceFileType.NOTEBOOK,
    parent_uuid="fld-work",
    page_count=3,
    last_modified=_MODIFIED,
)
_SECOND = DeviceDocument(
    uuid="b" * 36,
    name="Notes two",
    file_type=DeviceFileType.NOTEBOOK,
    page_count=1,
    last_modified=_MODIFIED,
)
_SKETCH = DeviceDocument(
    uuid="c" * 36,
    name="Sketch",
    file_type=DeviceFileType.NOTEBOOK,
)
"""The one document with no page count and no instant, so both cells are measured empty."""

_MANUAL = DeviceDocument(
    uuid="d" * 36,
    name="Manual [draft]",
    file_type=DeviceFileType.PDF,
    page_count=10,
    last_modified=_MODIFIED,
)
_TRASHED = DeviceDocument(
    uuid="e" * 36,
    name="Deleted notes",
    file_type=DeviceFileType.NOTEBOOK,
    page_count=1,
    trashed=True,
)

_DOCUMENTS = (_FIRST, _SECOND, _SKETCH, _MANUAL, _TRASHED)
_SKIPPED = (SkippedEntry(uuid="f" * 36, reason=SkipReason.UNREADABLE, detail="permission denied"),)


class _CatalogDouble(Provider):
    """Bind the shipped in-memory catalog over the real device binding.

    ``override=True`` on the one port :class:`~rmspec.app.ResolveDocument` reaches through is
    what keeps these tests off the wire: nothing in the resulting graph opens an SSH session or
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
    """Make every test independent of the developer's own shell.

    ``load_settings`` reads the real environment, so an exported ``RMSPEC_*`` would change what
    a test measures -- or fail it outright, since a bad value is an ``InvalidSettingError``.
    """
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    /,
    *,
    documents: tuple[DeviceDocument, ...] = _DOCUMENTS,
    skipped: tuple[SkippedEntry, ...] = (),
) -> None:
    """Wrap the command module's ``run`` so the in-memory catalog is bound for this call.

    ``providers=`` is ``run``'s test-only hook and a real command never passes it, so a test
    reaches it by rebinding the name the command calls.
    """
    catalog = InMemoryDeviceCatalog(documents=documents, skipped=skipped)

    def patched(
        body: Callable[[Invoked], int],
        /,
        *,
        json: bool = False,
        dense: bool = False,
    ) -> int:
        return _real_run(body, json=json, dense=dense, providers=[_CatalogDouble(catalog)])

    monkeypatch.setattr(_read, "run", patched)


def _document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json_module.loads(capsys.readouterr().out)


def _records(capsys: pytest.CaptureFixture[str]) -> list[list[str]]:
    return [line.split("\t") for line in capsys.readouterr().out.splitlines()]


# ──────────────────────────── the JSON envelope ────────────────────────────


def test_the_envelope_carries_the_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one", json=True) == 0
    document = _document(capsys)
    assert document["api_version"] == "rmspec/v1"
    assert document["type"] == READ_RESPONSE_TYPE == RESPONSE_TYPES["read"]
    data = document["data"]
    assert data["chosen"]["uuid"] == _FIRST.uuid
    assert data["chosen"]["page_count"] == 3
    assert data["also_matched"] == []


def test_a_uuid_prefix_resolves_the_same_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read(_MANUAL.uuid[:8], json=True) == 0
    data = _document(capsys)["data"]
    assert data["chosen"]["uuid"] == _MANUAL.uuid


def test_an_ambiguous_selector_succeeds_and_reports_every_other_match(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes", json=True) == 0
    document = _document(capsys)
    data = document["data"]
    assert data["chosen"]["uuid"] == _FIRST.uuid
    assert [entry["uuid"] for entry in data["also_matched"]] == [_SECOND.uuid]
    degradations = document["degradations"]
    assert degradations[0]["kind"] == "ambiguous_auto_resolved"
    assert degradations[0]["substituted"] == _FIRST.uuid


def test_strict_refuses_the_same_selector_and_carries_the_candidates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    expected = exit_code(AmbiguousDocument(query="Notes", candidates=()))
    assert read("Notes", strict=True, json=True) == expected
    error = _document(capsys)["error"]
    assert error["type"] == "AmbiguousDocument"
    assert [entry["uuid"] for entry in error["candidates"]] == [_SECOND.uuid]


def test_strict_is_silent_when_exactly_one_document_matched(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Sketch", strict=True, json=True) == 0
    assert _document(capsys)["degradations"] == []


def test_a_skipped_entry_is_hoisted_out_of_the_data(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, skipped=_SKIPPED)
    assert read("Sketch", json=True) == 0
    degradations = _document(capsys)["degradations"]
    assert degradations[0]["kind"] == "catalog_entry_skipped"


def test_the_trash_is_not_the_library(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Deleted", json=True) == 66
    error = _document(capsys)["error"]
    assert error["type"] == "DocumentNotFound"


def test_a_blank_selector_is_refused_before_the_catalog_is_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("   ", json=True) == 2
    error = _document(capsys)["error"]
    assert error["type"] == "UsageError"


def test_nothing_matching_an_incomplete_listing_is_unknown_rather_than_absent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch, skipped=_SKIPPED)
    assert read("Nowhere", json=True) == 69
    error = _document(capsys)["error"]
    assert error["type"] == "DocumentStoreUnavailable"


# ──────────────────────────── the next action ────────────────────────────


def test_handwriting_points_at_the_transcriber(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one", json=True) == 0
    action = _document(capsys)["next"]
    assert action["command"] == f"rmspec ocr {_FIRST.uuid} --json"
    assert action["purpose"]


def test_a_document_with_an_underlay_points_at_the_annotation_reader(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Manual", json=True) == 0
    action = _document(capsys)["next"]
    assert action["command"] == f"rmspec annotations {_MANUAL.uuid} --json"


def test_every_file_type_names_a_next_command():
    assert set(_read._NEXT_ACTIONS) == set(DeviceFileType)


# ──────────────────────────── the dense projection ────────────────────────────


def test_dense_writes_the_documented_header_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one", dense=True) == 0
    assert _records(capsys)[0] == list(DENSE_COLUMNS)


def test_dense_fills_every_cell_of_the_chosen_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one", dense=True) == 0
    record = _records(capsys)[1]
    assert record == [
        CHOSEN_CELL,
        _FIRST.uuid,
        _FIRST.name,
        DeviceFileType.NOTEBOOK.value,
        "3",
        "fld-work",
        _MODIFIED.isoformat(),
    ]


def test_dense_leaves_an_unreported_count_instant_and_parent_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Sketch", dense=True) == 0
    record = _records(capsys)[1]
    assert record[4:] == [ABSENT_CELL, ABSENT_CELL, ABSENT_CELL]


def test_dense_gives_each_other_match_its_own_record(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes", dense=True) == 0
    records = _records(capsys)
    assert len(records) == 3
    assert records[1][0] == CHOSEN_CELL
    assert records[2] == [
        OTHER_CELL,
        _SECOND.uuid,
        _SECOND.name,
        ABSENT_CELL,
        ABSENT_CELL,
        ABSENT_CELL,
        ABSENT_CELL,
    ]


def test_dense_keeps_its_record_stream_clean_and_warns_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes", dense=True) == 0
    captured = capsys.readouterr()
    assert "ambiguous_auto_resolved" in captured.err
    assert "ambiguous_auto_resolved" not in captured.out


# ──────────────────────────── the human rendering ────────────────────────────


def test_a_human_run_puts_the_facts_on_stderr_and_nothing_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one") == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"uuid: {_FIRST.uuid}" in captured.err
    assert "page_count: 3" in captured.err
    assert f"last_modified: {_MODIFIED.isoformat()}" in captured.err


def test_a_human_run_keeps_a_bracketed_name_intact(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Manual") == 0
    assert "name: Manual [draft]" in capsys.readouterr().err


def test_a_human_run_lists_the_other_matches_under_a_heading(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes") == 0
    stderr = capsys.readouterr().err
    assert "also matched:" in stderr
    assert f"  {_SECOND.uuid}  {_SECOND.name}" in stderr


def test_an_unambiguous_human_run_says_nothing_about_other_matches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Sketch") == 0
    assert "also matched" not in capsys.readouterr().err


def test_a_human_run_names_the_next_command_too(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Sketch") == 0
    assert f"next: rmspec ocr {_SKETCH.uuid} --json" in capsys.readouterr().err


# ──────────────────────────── the declared surface ────────────────────────────


def test_two_output_modes_are_refused_by_the_shared_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    _bind(monkeypatch)
    assert read("Notes one", json=True, dense=True) == 2
    error = _document(capsys)["error"]
    assert error["type"] == "UsageError"


def test_the_manifest_can_describe_the_command_the_orchestrator_will_register():
    built = App(name="probe")
    built.command(read, name="read")
    described = _describe_commands(built, ())[0]
    assert described["name"] == "read"
    assert described["help"] == (
        "Resolve one document selector and report what the catalog knows about it."
    )
    assert described["response_types"] == [READ_RESPONSE_TYPE]
    assert described["modes"] == ["human", "json", "dense"]
    parameters = {entry["name"]: entry for entry in described["parameters"]}
    assert parameters["doc"]["flags"] == ["DOC"]
    assert parameters["doc"]["kind"] == "positional"
    assert parameters["doc"]["required"] is True
    assert parameters["strict"]["negative_flags"] == []
    assert all(entry["help"] for entry in described["parameters"])
