"""``rmspec sync``: one pull, one prediction, one history read, and an honest exit status."""

from __future__ import annotations

import datetime
import json as json_module
import os
from functools import partial
from typing import Any

import pytest
from dishka import Provider, Scope, provide

from rmspec.cli import _sync as sync_module
from rmspec.cli._invoke import run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.cli._sync import (
    CLEAN_OUTCOMES,
    DOCUMENT_COLUMNS,
    HISTORY_COLUMNS,
    HISTORY_RESPONSE_TYPE,
    INCOMPLETE_EXIT_STATUS,
    _perform,
    now,
    sync,
)
from rmspec.device.testing import (
    IN_MEMORY_ENDPOINT,
    IN_MEMORY_TRANSPORT,
    InMemoryDeviceCatalog,
    InMemoryRawBundleSource,
)
from rmspec.domain.errors import DeviceUnreachable
from rmspec.domain.models import SyncAuditEntry, SyncOperation, SyncOutcome
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DevicePageSource,
    RawBundleSource,
    SkippedEntry,
    SkipReason,
)
from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog
from rmspec.persistence.testing import InMemoryDocumentSyncStore, InMemorySyncAuditLog

_NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)
_LATER = datetime.datetime(2026, 8, 30, 9, 30, tzinfo=datetime.UTC)
_FROZEN = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)

_DOC_A = "a" * 8
_DOC_B = "b" * 8
_PAGE_A = "pa" * 4
_PAGE_B = "pb" * 4

_PAGES = {
    _DOC_A: (DevicePageSource(page_id=_PAGE_A, scene=b"scene for a"),),
    _DOC_B: (DevicePageSource(page_id=_PAGE_B, scene=b"scene for b"),),
}


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ``RMSPEC_*`` shell out of every measurement here.

    ``load_settings`` reads the real environment, so an exported variable would change what a
    test measures or fail it outright. Pinning the native-library variable stops
    ``apply_native_library_path`` mutating the interpreter these tests run in.
    """
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold :func:`~rmspec.cli._sync.now` still, so a run's rows are assertable.

    The command reads its instant through one module-level function precisely so that this is
    possible: a body that called ``datetime.now`` inline could not be checked against the rows
    it wrote, and the assertion would be flaky under ``-n auto``.

    Patched on the module object rather than by dotted string: pytest resolves a string target
    with ``getattr`` on each parent, and another test in this package reloads ``rmspec.cli``,
    which drops the submodule attribute the walk needs.
    """
    monkeypatch.setattr(sync_module, "now", lambda: _FROZEN)


class _Doubles(Provider):
    """Bind the shipped in-memory catalog, bundle source, mirror and history.

    ``override=True`` on the four ports :class:`~rmspec.app.SyncDocuments` reaches through is
    what keeps these tests off the wire and off the developer's own SQLite file: nothing in the
    resulting graph opens a database, an SSH session or a socket to the tablet.
    """

    scope = Scope.REQUEST

    def __init__(
        self,
        catalog: InMemoryDeviceCatalog,
        bundles: InMemoryRawBundleSource,
        store: InMemoryDocumentSyncStore,
        audit: InMemorySyncAuditLog,
    ) -> None:
        super().__init__()
        self._catalog = catalog
        self._bundles = bundles
        self._store = store
        self._audit = audit

    @provide(provides=DeviceCatalog, override=True)
    def catalog(self) -> InMemoryDeviceCatalog:
        return self._catalog

    @provide(provides=RawBundleSource, override=True)
    def bundles(self) -> InMemoryRawBundleSource:
        return self._bundles

    @provide(provides=DocumentSyncStore, override=True)
    def mirror(self) -> InMemoryDocumentSyncStore:
        return self._store

    @provide(provides=SyncAuditLog, override=True)
    def history(self) -> InMemorySyncAuditLog:
        return self._audit


def _doc(
    uuid: str,
    name: str,
    /,
    *,
    at: datetime.datetime | None = _NOW,
    pages: int = 1,
) -> DeviceDocument:
    """Build one listing entry, timestamped so change detection has something to compare."""
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        last_modified=at,
        page_count=pages,
    )


def _providers(
    *,
    documents: tuple[DeviceDocument, ...] = (),
    skipped: tuple[SkippedEntry, ...] = (),
    seeded_pages: bool = True,
    truncate_at: int | None = None,
    catalog_fails: bool = False,
    store: InMemoryDocumentSyncStore | None = None,
    audit: InMemorySyncAuditLog | None = None,
) -> list[Provider]:
    """Assemble one invocation's doubles.

    Parameters
    ----------
    documents
        The listing the tablet reports.
    skipped
        Entries the listing could not represent, which block the prune.
    seeded_pages
        Whether the bundle source holds pages for those documents. ``False`` makes every
        fetched bundle come back empty, which is the refusal a recorded page set exists to
        provoke.
    truncate_at
        Bytes to report before raising ``DeviceTransferInterrupted`` on every fetch.
    catalog_fails
        Whether the enumeration itself raises ``DeviceUnreachable``, which must propagate.
    store
        A mirror to reuse across two runs, or ``None`` for a fresh one.
    audit
        A history to reuse or pre-seed, or ``None`` for an empty one.

    Returns
    -------
    list[Provider]
        The one provider to pass as ``run(..., providers=...)``.
    """
    catalog = InMemoryDeviceCatalog(
        documents=documents,
        skipped=skipped,
        fail_with=(
            DeviceUnreachable(
                transport=IN_MEMORY_TRANSPORT,
                endpoint=IN_MEMORY_ENDPOINT,
                detail="the cable is out",
            )
            if catalog_fails
            else None
        ),
    )
    bundles = InMemoryRawBundleSource(
        catalog=catalog,
        pages=_PAGES if seeded_pages else None,
        truncate_at=truncate_at,
    )
    return [
        _Doubles(
            catalog,
            bundles,
            store if store is not None else InMemoryDocumentSyncStore(),
            audit if audit is not None else InMemorySyncAuditLog(),
        )
    ]


def _sync(
    *,
    dry_run: bool = False,
    history: bool = False,
    limit: int | None = None,
    json: bool = False,
    dense: bool = False,
    providers: list[Provider],
) -> int:
    """Drive the command body through the shared boundary with the doubles bound.

    ``run(body, providers=...)`` is the documented test-only seam.
    :func:`~rmspec.cli._sync.sync` itself is exercised separately, below.
    """
    return run(
        partial(_perform, dry_run=dry_run, history=history, limit=limit),
        json=json,
        dense=dense,
        providers=providers,
    )


def _document(captured: str) -> dict[str, Any]:
    """Parse the one envelope a ``--json`` run wrote to stdout."""
    return json_module.loads(captured)


# ------------------------------------ a real pull ------------------------------------


def test_a_first_pull_records_every_listed_document(capsys: pytest.CaptureFixture[str]):
    store = InMemoryDocumentSyncStore()
    providers = _providers(
        documents=(_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B")), store=store
    )
    assert _sync(json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["type"] == RESPONSE_TYPES["sync"] == "sync"
    assert document["data"]["outcome"] == "succeeded"
    assert document["data"]["dry_run"] is False
    assert [entry["outcome"] for entry in document["data"]["documents"]] == [
        "succeeded",
        "succeeded",
    ]
    assert [entry["pages_recorded"] for entry in document["data"]["documents"]] == [1, 1]
    assert {row.uuid for row in store.list_documents()} == {_DOC_A, _DOC_B}


def test_a_pull_stamps_every_row_with_the_instant_the_clock_gave(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    assert _sync(providers=_providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)) == 0
    capsys.readouterr()
    assert [row.synced_at for row in store.list_documents()] == [_FROZEN]
    assert [page.synced_at for page in store.pages(_DOC_A)] == [_FROZEN]


def test_a_second_pull_of_an_unchanged_library_performs_no_transfer(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    documents = (_doc(_DOC_A, "Notes A"),)
    assert _sync(providers=_providers(documents=documents, store=store)) == 0
    capsys.readouterr()
    assert _sync(json=True, providers=_providers(documents=documents, store=store)) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "skipped"
    assert document["data"]["documents"][0]["changed"] is False
    assert document["data"]["documents"][0]["detail"] == "already current"


def test_a_rename_moves_the_signal_and_is_pulled_again(capsys: pytest.CaptureFixture[str]):
    store = InMemoryDocumentSyncStore()
    assert _sync(providers=_providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)) == 0
    capsys.readouterr()
    renamed = (_doc(_DOC_A, "Notes A, renamed"),)
    assert _sync(json=True, providers=_providers(documents=renamed, store=store)) == 0
    entry = _document(capsys.readouterr().out)["data"]["documents"][0]
    assert entry["outcome"] == "succeeded"
    assert entry["changed"] is True
    assert entry["pages_changed"] == 0


# --------------------------------- the exit-status rule ---------------------------------


def test_a_run_where_every_document_failed_does_not_exit_zero(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(
        documents=(_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B")),
        truncate_at=3,
    )
    assert _sync(json=True, providers=providers) == INCOMPLETE_EXIT_STATUS
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "failed"
    assert [entry["outcome"] for entry in document["data"]["documents"]] == ["failed", "failed"]
    assert "DeviceTransferInterrupted" in document["data"]["documents"][0]["detail"]


def test_a_partial_run_exits_non_zero_and_still_names_what_happened(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    first = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=first, store=store)) == 0
    capsys.readouterr()
    moved = (_doc(_DOC_A, "Notes A", at=_LATER), _doc(_DOC_B, "Notes B"))
    providers = _providers(documents=moved, seeded_pages=False, store=store)
    assert _sync(json=True, providers=providers) == INCOMPLETE_EXIT_STATUS
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "partial"
    assert [entry["outcome"] for entry in document["data"]["documents"]] == ["failed", "skipped"]
    assert "discard every page's recorded text" in document["data"]["documents"][0]["detail"]
    assert [row.page_uuid for row in store.pages(_DOC_A)] == [_PAGE_A]


def test_a_dry_run_exits_zero_even_though_it_landed_nothing(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),))
    assert _sync(dry_run=True, json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "skipped"
    assert SyncOutcome.SKIPPED in CLEAN_OUTCOMES


def test_the_clean_outcomes_are_an_allow_list_that_excludes_partial_and_failed():
    assert set(CLEAN_OUTCOMES) == {SyncOutcome.SUCCEEDED, SyncOutcome.SKIPPED}
    assert INCOMPLETE_EXIT_STATUS != 0


# ------------------------------------- the dry run -------------------------------------


def test_a_dry_run_writes_nothing_and_predicts_the_fetch(capsys: pytest.CaptureFixture[str]):
    store = InMemoryDocumentSyncStore()
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)
    assert _sync(dry_run=True, json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["dry_run"] is True
    assert document["data"]["documents"][0]["changed"] is True
    assert document["data"]["documents"][0]["pages_recorded"] == 0
    assert document["next"]["command"] == "rmspec sync"
    assert store.list_documents() == []


def test_a_dry_run_of_a_current_library_suggests_nothing(capsys: pytest.CaptureFixture[str]):
    store = InMemoryDocumentSyncStore()
    documents = (_doc(_DOC_A, "Notes A"),)
    assert _sync(providers=_providers(documents=documents, store=store)) == 0
    capsys.readouterr()
    providers = _providers(documents=documents, store=store)
    assert _sync(dry_run=True, json=True, providers=providers) == 0
    assert "next" not in _document(capsys.readouterr().out)


def test_the_predicted_pull_reaches_a_human_on_stderr(capsys: pytest.CaptureFixture[str]):
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),))
    assert _sync(dry_run=True, providers=providers) == 0
    assert "next: rmspec sync" in capsys.readouterr().err


# -------------------------- absent is not forgotten --------------------------


def test_a_complete_listing_forgets_what_the_tablet_no_longer_has(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    both = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=both, store=store)) == 0
    capsys.readouterr()
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)
    assert _sync(json=True, providers=providers) == 0
    data = _document(capsys.readouterr().out)["data"]
    assert data["absent"] == [_DOC_B]
    assert data["forgotten"] == [_DOC_B]
    assert store.get_document(_DOC_B) is None


def test_a_dry_run_names_what_it_would_forget_and_forgets_nothing(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    both = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=both, store=store)) == 0
    capsys.readouterr()
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)
    assert _sync(dry_run=True, json=True, providers=providers) == 0
    data = _document(capsys.readouterr().out)["data"]
    assert data["absent"] == [_DOC_B]
    assert data["forgotten"] == []
    assert store.get_document(_DOC_B) is not None


def test_an_unrepresentable_entry_blocks_the_prune_and_is_reported_as_a_degradation(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    both = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=both, store=store)) == 0
    capsys.readouterr()
    providers = _providers(
        documents=(_doc(_DOC_A, "Notes A"),),
        skipped=(SkippedEntry(uuid=None, reason=SkipReason.UNREADABLE, detail="torn"),),
        store=store,
    )
    assert _sync(json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["absent"] == [_DOC_B]
    assert document["data"]["forgotten"] == []
    assert [item["kind"] for item in document["degradations"]] == ["catalog_entry_skipped"]


def test_a_refused_prune_is_said_out_loud_to_a_human(capsys: pytest.CaptureFixture[str]):
    store = InMemoryDocumentSyncStore()
    both = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=both, store=store)) == 0
    capsys.readouterr()
    providers = _providers(
        documents=(_doc(_DOC_A, "Notes A"),),
        store=store,
    )
    assert _sync(dry_run=True, providers=providers) == 0
    assert "absent but not forgotten" in capsys.readouterr().err


def test_a_pull_with_nothing_to_prune_says_nothing_about_pruning(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),))
    assert _sync(providers=providers) == 0
    assert "absent but not forgotten" not in capsys.readouterr().err


# ------------------------------ the dense projections ------------------------------


def test_dense_separates_listed_absent_and_forgotten_by_a_leading_kind(
    capsys: pytest.CaptureFixture[str],
):
    store = InMemoryDocumentSyncStore()
    both = (_doc(_DOC_A, "Notes A"), _doc(_DOC_B, "Notes B"))
    assert _sync(providers=_providers(documents=both, store=store)) == 0
    capsys.readouterr()
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),), store=store)
    assert _sync(dense=True, providers=providers) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t") == list(DOCUMENT_COLUMNS)
    assert [line.split("\t")[0] for line in lines[1:]] == ["document", "absent", "forgotten"]
    assert lines[2].split("\t") == ["absent", _DOC_B, "", "", "", "", ""]


def test_dense_spells_a_changed_flag_the_way_json_does(capsys: pytest.CaptureFixture[str]):
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),))
    assert _sync(dense=True, providers=providers) == 0
    cells = capsys.readouterr().out.splitlines()[1].split("\t")
    assert cells == ["document", _DOC_A, "Notes A", "succeeded", "true", "1", ""]


def test_dense_keeps_its_stdout_homogeneous_when_a_degradation_is_reported(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(
        documents=(_doc(_DOC_A, "Notes A"),),
        skipped=(SkippedEntry(uuid=None, reason=SkipReason.UNREADABLE, detail="torn"),),
    )
    assert _sync(dense=True, providers=providers) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == "\t".join(DOCUMENT_COLUMNS)
    assert "catalog_entry_skipped" in captured.err


# -------------------------------- the human rendering --------------------------------


def test_human_puts_its_table_on_stderr_and_leaves_stdout_empty(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(documents=(_doc(_DOC_A, "Notes A"),))
    assert _sync(providers=providers) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Notes A" in captured.err
    assert "pull succeeded" in captured.err


def test_human_falls_back_to_the_uuid_for_a_document_with_no_name(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(documents=(_doc(_DOC_A, ""),))
    assert _sync(providers=providers) == 0
    assert _DOC_A in capsys.readouterr().err


# ------------------------- whole-transport failure propagates -------------------------


def test_an_unreachable_tablet_is_a_failure_and_not_an_empty_library(
    capsys: pytest.CaptureFixture[str],
):
    assert _sync(json=True, providers=_providers(catalog_fails=True)) == 69
    assert _document(capsys.readouterr().out)["error"]["type"] == "DeviceUnreachable"


# ----------------------------------- the history read -----------------------------------


def _seeded_history(count: int, /) -> InMemorySyncAuditLog:
    audit = InMemorySyncAuditLog()
    for index in range(count):
        audit.append(
            SyncAuditEntry(
                operation=SyncOperation.PULL,
                outcome=SyncOutcome.SUCCEEDED,
                doc_uuid=_DOC_A,
                doc_name="Notes A",
                pages_affected=index,
                occurred_at=_NOW,
            )
        )
    return audit


def test_history_emits_its_own_discriminator_rather_than_the_pull_one(
    capsys: pytest.CaptureFixture[str],
):
    providers = _providers(audit=_seeded_history(2))
    assert _sync(history=True, json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["type"] == HISTORY_RESPONSE_TYPE == "history"
    assert document["type"] != RESPONSE_TYPES["sync"]
    assert [record["sequence"] for record in document["data"]["entries"]] == [2, 1]
    assert document["data"]["limit"] == 20
    assert document["data"]["latest_sequence"] == 2


def test_history_needs_no_tablet_and_never_degrades(capsys: pytest.CaptureFixture[str]):
    providers = _providers(catalog_fails=True, audit=_seeded_history(1))
    assert _sync(history=True, json=True, providers=providers) == 0
    assert _document(capsys.readouterr().out)["degradations"] == []


def test_history_honours_a_limit(capsys: pytest.CaptureFixture[str]):
    providers = _providers(audit=_seeded_history(5))
    assert _sync(history=True, limit=2, json=True, providers=providers) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["limit"] == 2
    assert len(document["data"]["entries"]) == 2


def test_a_page_above_the_use_cases_ceiling_is_refused_rather_than_trimmed(
    capsys: pytest.CaptureFixture[str],
):
    assert _sync(history=True, limit=501, json=True, providers=_providers()) == 2
    error = _document(capsys.readouterr().out)["error"]
    assert error["type"] == "UsageError"
    assert "refused rather than" in error["message"]


def test_a_limit_of_zero_is_a_usage_error_and_not_a_validation_error(
    capsys: pytest.CaptureFixture[str],
):
    assert _sync(history=True, limit=0, json=True, providers=_providers()) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_history_dense_writes_the_documented_columns(capsys: pytest.CaptureFixture[str]):
    providers = _providers(audit=_seeded_history(1))
    assert _sync(history=True, dense=True, providers=providers) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].split("\t") == list(HISTORY_COLUMNS)
    assert lines[1].split("\t") == [
        "1",
        _NOW.isoformat(),
        "pull",
        "succeeded",
        _DOC_A,
        "Notes A",
        "0",
        "",
    ]
    assert "latest sequence 1" in captured.err


def test_history_dense_leaves_a_library_wide_entry_with_no_document_cell(
    capsys: pytest.CaptureFixture[str],
):
    audit = InMemorySyncAuditLog()
    audit.append(
        SyncAuditEntry(
            operation=SyncOperation.OCR,
            outcome=SyncOutcome.SUCCEEDED,
            occurred_at=_NOW,
        )
    )
    assert _sync(history=True, dense=True, providers=_providers(audit=audit)) == 0
    assert capsys.readouterr().out.splitlines()[1].split("\t")[4] == ""


def test_history_human_puts_its_table_on_stderr(capsys: pytest.CaptureFixture[str]):
    providers = _providers(audit=_seeded_history(1))
    assert _sync(history=True, providers=providers) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sync history" in captured.err
    assert "Notes A" in captured.err


def test_history_human_falls_back_to_the_uuid_when_an_entry_has_no_name(
    capsys: pytest.CaptureFixture[str],
):
    audit = InMemorySyncAuditLog()
    audit.append(
        SyncAuditEntry(
            operation=SyncOperation.PULL,
            outcome=SyncOutcome.SUCCEEDED,
            doc_uuid=_DOC_A,
            occurred_at=_NOW,
        )
    )
    assert _sync(history=True, providers=_providers(audit=audit)) == 0
    assert _DOC_A in capsys.readouterr().err


def test_history_human_says_a_library_wide_entry_names_no_document(
    capsys: pytest.CaptureFixture[str],
):
    audit = InMemorySyncAuditLog()
    audit.append(
        SyncAuditEntry(
            operation=SyncOperation.EXPORT,
            outcome=SyncOutcome.SUCCEEDED,
            occurred_at=_NOW,
        )
    )
    assert _sync(history=True, providers=_providers(audit=audit)) == 0
    assert "export" in capsys.readouterr().err


def test_an_empty_history_reports_no_latest_sequence(capsys: pytest.CaptureFixture[str]):
    assert _sync(history=True, json=True, providers=_providers()) == 0
    assert _document(capsys.readouterr().out)["data"]["latest_sequence"] is None


def test_an_empty_history_says_so_to_a_human(capsys: pytest.CaptureFixture[str]):
    assert _sync(history=True, providers=_providers()) == 0
    assert "latest sequence none" in capsys.readouterr().err


# ------------------------------ the refused combinations ------------------------------


def test_history_with_dry_run_is_refused_rather_than_resolved(
    capsys: pytest.CaptureFixture[str],
):
    assert _sync(history=True, dry_run=True, json=True, providers=_providers()) == 2
    error = _document(capsys.readouterr().out)["error"]
    assert error["type"] == "UsageError"
    assert "--history with --dry-run" in error["message"]


def test_limit_without_history_is_refused_rather_than_ignored(
    capsys: pytest.CaptureFixture[str],
):
    assert _sync(limit=5, json=True, providers=_providers()) == 2
    error = _document(capsys.readouterr().out)["error"]
    assert error["type"] == "UsageError"
    assert "--limit without --history" in error["message"]


def test_two_output_modes_are_refused(capsys: pytest.CaptureFixture[str]):
    assert _sync(json=True, dense=True, providers=_providers()) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_the_command_itself_goes_through_the_shared_boundary(
    capsys: pytest.CaptureFixture[str],
):
    """Drive the real command, on the one path that answers before a container exists.

    ``run`` renders a contradictory output mode before it composes anything, so this reaches
    :func:`~rmspec.cli._sync.sync` without a tablet, a database or a socket.
    """
    assert sync(dry_run=True, json=True, dense=True) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


# --------------------------------------- the clock ---------------------------------------


def test_the_clock_hands_back_an_aware_instant():
    """The imported name is the real function; the fixture patches the module attribute."""
    assert now().tzinfo is not None
    assert now().utcoffset() == datetime.timedelta(0)
