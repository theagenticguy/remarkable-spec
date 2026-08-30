"""``rmspec search``: two sources, one substring matcher, and an honest empty answer."""

from __future__ import annotations

import datetime
import json as json_module
import os
from functools import partial
from typing import Any

import pytest
from dishka import Provider, Scope, provide

from rmspec.cli._invoke import run
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._search import DENSE_COLUMNS, EXCERPT_WIDTH, _excerpt, _report, search
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.domain.models import (
    DocumentKind,
    PageText,
    SyncAuditEntry,
    SyncedDocument,
    SyncedPage,
    SyncOperation,
    SyncOutcome,
    TextProvenance,
)
from rmspec.domain.ports.ocr import HandwrittenTextIndex
from rmspec.domain.ports.persistence import DocumentSyncStore, SyncAuditLog
from rmspec.persistence.testing import (
    FakeHandwrittenTextIndex,
    InMemoryDocumentSyncStore,
    InMemorySyncAuditLog,
)

_NOW = datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.UTC)

_DOC = "d" * 8
_OTHER = "e" * 8
_PAGE_ONE = "p" * 8
_PAGE_TWO = "q" * 8

_MIRROR_TEXT = "the retention policy is thirty days"
_DEVICE_TEXT = "the retention polrcy is thirty days"


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


class _Doubles(Provider):
    """Bind the shipped in-memory mirror, index and history over the real bindings.

    ``override=True`` on the three ports :class:`~rmspec.app.SearchText` reaches through is
    what keeps these tests off the wire and off the developer's own SQLite file: nothing in the
    resulting graph opens a database, an SSH session, or a model client.
    """

    scope = Scope.REQUEST

    def __init__(
        self,
        store: InMemoryDocumentSyncStore,
        index: FakeHandwrittenTextIndex,
        audit: InMemorySyncAuditLog,
    ) -> None:
        super().__init__()
        self._store = store
        self._index = index
        self._audit = audit

    @provide(provides=DocumentSyncStore, override=True)
    def mirror(self) -> InMemoryDocumentSyncStore:
        return self._store

    @provide(provides=HandwrittenTextIndex, override=True)
    def handwriting(self) -> FakeHandwrittenTextIndex:
        return self._index

    @provide(provides=SyncAuditLog, override=True)
    def history(self) -> InMemorySyncAuditLog:
        return self._audit


def _mirror(
    *,
    documents: tuple[tuple[str, str, int], ...] = ((_DOC, "Retention notes", 2),),
    texts: tuple[tuple[str, str, int, str], ...] = (),
) -> InMemoryDocumentSyncStore:
    """Build a mirror holding the given documents, their pages, and their recorded text.

    Parameters
    ----------
    documents
        ``(doc_uuid, visible_name, page_count)`` per document. Two pages are recorded for
        every document, so a scope always has pages to search.
    texts
        ``(doc_uuid, page_uuid, page_index, text)`` per recorded transcription.

    Returns
    -------
    InMemoryDocumentSyncStore
        The seeded double.
    """
    store = InMemoryDocumentSyncStore()
    for uuid, name, count in documents:
        store.record_document(
            SyncedDocument(
                uuid=uuid,
                visible_name=name,
                kind=DocumentKind.DOCUMENT,
                page_count=count,
                synced_at=_NOW,
            ),
            [
                SyncedPage(doc_uuid=uuid, page_uuid=_PAGE_ONE, page_index=0, synced_at=_NOW),
                SyncedPage(doc_uuid=uuid, page_uuid=_PAGE_TWO, page_index=1, synced_at=_NOW),
            ],
        )
    for uuid, page_uuid, page_index, text in texts:
        store.record_page_text(
            PageText(
                doc_uuid=uuid,
                page_uuid=page_uuid,
                page_index=page_index,
                text=text,
                provenance=TextProvenance(recognizers=("apple_vision",), extracted_at=_NOW),
            )
        )
    return store


def _empty_mirror() -> InMemoryDocumentSyncStore:
    return _mirror(documents=())


def _providers(
    store: InMemoryDocumentSyncStore,
    *,
    index: FakeHandwrittenTextIndex | None = None,
    audit: InMemorySyncAuditLog | None = None,
) -> list[Provider]:
    return [_Doubles(store, index or FakeHandwrittenTextIndex(), audit or InMemorySyncAuditLog())]


def _search(
    query: str,
    *,
    doc: str | None = None,
    json: bool = False,
    dense: bool = False,
    providers: list[Provider],
) -> int:
    """Drive the command body through the shared boundary with the doubles bound.

    ``run(body, providers=...)`` is the documented test-only seam: the doubles are appended
    after the container's defaults with ``override=True`` on each ``provide``, so no test here
    opens a database, an SSH session or a model client. :func:`~rmspec.cli._search.search`
    itself is exercised separately, below.
    """
    return run(
        partial(_report, query=query, doc=doc),
        json=json,
        dense=dense,
        providers=providers,
    )


def _document(captured: str) -> dict[str, Any]:
    """Parse the one envelope a ``--json`` run wrote to stdout."""
    return json_module.loads(captured)


# --------------------------- the two sources, attributed ---------------------------


def test_a_mirror_hit_names_the_mirror_and_carries_its_provenance(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", json=True, providers=_providers(store)) == 0
    document = _document(capsys.readouterr().out)
    assert document["type"] == RESPONSE_TYPES["search"] == "matches"
    matches = document["data"]["matches"]
    assert [match["source"] for match in matches] == ["mirror"]
    assert matches[0]["provenance"]["recognizers"] == ["apple_vision"]
    assert matches[0]["index_generation"] is None
    assert matches[0]["corroborated"] is False


def test_a_device_index_hit_names_the_index_and_carries_no_provenance(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex(generation=7)
    index.seed(_PAGE_TWO, entry_ref=_DOC, text=_DEVICE_TEXT)
    assert _search("thirty", json=True, providers=_providers(_mirror(), index=index)) == 0
    matches = _document(capsys.readouterr().out)["data"]["matches"]
    assert [match["source"] for match in matches] == ["device_index"]
    assert matches[0]["provenance"] is None
    assert matches[0]["index_generation"] == 7


def test_a_page_both_sources_read_is_reported_twice_and_marked_corroborated(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex()
    index.seed(_PAGE_ONE, entry_ref=_DOC, text=_DEVICE_TEXT)
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("thirty", json=True, providers=_providers(store, index=index)) == 0
    matches = _document(capsys.readouterr().out)["data"]["matches"]
    assert [match["source"] for match in matches] == ["mirror", "device_index"]
    assert all(match["corroborated"] for match in matches)
    assert {match["text"] for match in matches} == {_MIRROR_TEXT, _DEVICE_TEXT}


def test_the_substring_matcher_is_case_insensitive(capsys: pytest.CaptureFixture[str]):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("RETENTION", json=True, providers=_providers(store)) == 0
    assert len(_document(capsys.readouterr().out)["data"]["matches"]) == 1


# ------------------------------- the dense projection -------------------------------


def test_dense_writes_the_documented_columns_with_the_reading_last(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", dense=True, providers=_providers(store)) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].split("\t") == list(DENSE_COLUMNS)
    assert lines[1].split("\t") == [
        _DOC,
        "Retention notes",
        "0",
        "mirror",
        "false",
        _MIRROR_TEXT,
    ]
    assert "matched:" in captured.err


def test_dense_spells_a_corroborated_flag_the_way_json_does(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex()
    index.seed(_PAGE_ONE, entry_ref=_DOC, text=_DEVICE_TEXT)
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("thirty", dense=True, providers=_providers(store, index=index)) == 0
    rows = capsys.readouterr().out.splitlines()[1:]
    assert [row.split("\t")[4] for row in rows] == ["true", "true"]


def test_dense_carries_the_whole_reading_rather_than_the_human_excerpt(
    capsys: pytest.CaptureFixture[str],
):
    long_text = f"{'padding ' * 40}retention{' trailing' * 40}"
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, long_text),))
    assert _search("retention", dense=True, providers=_providers(store)) == 0
    cells = capsys.readouterr().out.splitlines()[1].split("\t")
    assert cells[5] == long_text
    assert len(cells[5]) > EXCERPT_WIDTH


def test_dense_writes_only_its_header_when_nothing_matched(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("mermaid", dense=True, providers=_providers(store)) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["\t".join(DENSE_COLUMNS)]
    assert "no_match:" in captured.err


# ---------------------------------- the human table ----------------------------------


def test_human_puts_its_table_on_stderr_and_leaves_stdout_empty(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", providers=_providers(store)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Retention notes" in captured.err
    assert "mirror" in captured.err


def test_human_ticks_only_the_corroborated_rows(capsys: pytest.CaptureFixture[str]):
    index = FakeHandwrittenTextIndex()
    index.seed(_PAGE_ONE, entry_ref=_DOC, text=_DEVICE_TEXT)
    store = _mirror(
        texts=(
            (_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),
            (_DOC, _PAGE_TWO, 1, _MIRROR_TEXT),
        ),
    )
    assert _search("thirty", providers=_providers(store, index=index)) == 0
    rendered = capsys.readouterr().err
    assert rendered.count("yes") == 2


def test_human_windows_a_long_reading_around_the_term(capsys: pytest.CaptureFixture[str]):
    long_text = f"{'padding ' * 40}retention{' trailing' * 40}"
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, long_text),))
    assert _search("retention", providers=_providers(store)) == 0
    assert "..." in capsys.readouterr().err


# ------------------------------ what an absence means ------------------------------


def test_no_recorded_page_reports_nothing_synced_and_suggests_a_pull(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("retention", json=True, providers=_providers(_empty_mirror())) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "nothing_synced"
    assert document["data"]["pages_searched"] == 0
    assert document["data"]["recent_ocr_attempt"] is None
    assert document["next"]["command"] == "rmspec sync"


def test_an_untranscribed_scope_named_by_doc_suggests_transcribing_that_document(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("retention", doc=_DOC, json=True, providers=_providers(_mirror())) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "nothing_transcribed"
    assert document["data"]["recent_ocr_attempt"] is False
    assert document["next"]["command"] == f"rmspec ocr {_DOC}"


def test_an_untranscribed_scope_with_no_document_named_suggests_nothing(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("retention", json=True, providers=_providers(_mirror())) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "nothing_transcribed"
    assert "next" not in document


def test_a_recent_ocr_run_in_history_is_reported_as_evidence(
    capsys: pytest.CaptureFixture[str],
):
    audit = InMemorySyncAuditLog()
    audit.append(
        SyncAuditEntry(
            operation=SyncOperation.OCR,
            outcome=SyncOutcome.SUCCEEDED,
            doc_uuid=_DOC,
            occurred_at=_NOW,
        )
    )
    assert _search("retention", providers=_providers(_mirror(), audit=audit)) == 0
    assert "an ocr run" in capsys.readouterr().err


def test_no_ocr_run_in_history_is_reported_as_the_absence_of_evidence(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("retention", providers=_providers(_mirror())) == 0
    assert "no ocr run" in capsys.readouterr().err


def test_a_term_no_reading_contains_is_no_match_and_still_exits_zero(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("mermaid", json=True, providers=_providers(store)) == 0
    document = _document(capsys.readouterr().out)
    assert document["data"]["outcome"] == "no_match"
    assert document["data"]["pages_searched"] == 2
    assert document["data"]["recent_ocr_attempt"] is None
    assert "next" not in document


def test_the_next_command_reaches_a_human_on_stderr(capsys: pytest.CaptureFixture[str]):
    assert _search("retention", providers=_providers(_empty_mirror())) == 0
    assert "next: rmspec sync" in capsys.readouterr().err


# --------------------------------- scope and refusals ---------------------------------


def test_doc_restricts_the_search_to_one_recorded_document(
    capsys: pytest.CaptureFixture[str],
):
    store = _mirror(
        documents=((_DOC, "Retention notes", 2), (_OTHER, "Other notes", 2)),
        texts=(
            (_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),
            (_OTHER, _PAGE_ONE, 0, _MIRROR_TEXT),
        ),
    )
    assert _search("retention", doc=_OTHER, json=True, providers=_providers(store)) == 0
    matches = _document(capsys.readouterr().out)["data"]["matches"]
    assert [match["doc_uuid"] for match in matches] == [_OTHER]


def test_a_blank_term_is_refused_before_any_store_is_read(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("   ", json=True, providers=_providers(_mirror())) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_a_doc_the_mirror_does_not_track_is_document_not_found(
    capsys: pytest.CaptureFixture[str],
):
    assert _search("retention", doc=_OTHER, json=True, providers=_providers(_mirror())) == 66
    assert _document(capsys.readouterr().out)["error"]["type"] == "DocumentNotFound"


def test_two_output_modes_are_refused(capsys: pytest.CaptureFixture[str]):
    assert _search("retention", json=True, dense=True, providers=_providers(_mirror())) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


def test_the_command_itself_goes_through_the_shared_boundary(
    capsys: pytest.CaptureFixture[str],
):
    """Drive the real command, on the one path that answers before a container exists.

    ``run`` renders a contradictory output mode before it composes anything, so this reaches
    :func:`~rmspec.cli._search.search` without a tablet, a database or a model client.
    """
    assert search("retention", doc=_DOC, json=True, dense=True) == 2
    assert _document(capsys.readouterr().out)["error"]["type"] == "UsageError"


# ------------------------- the free prior degrades, never fails -------------------------


def test_an_unreadable_device_index_degrades_and_the_mirror_still_answers(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex()
    index.fail_reads = True
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", json=True, providers=_providers(store, index=index)) == 0
    document = _document(capsys.readouterr().out)
    assert [item["kind"] for item in document["degradations"]] == ["device_index_unavailable"]
    assert [match["source"] for match in document["data"]["matches"]] == ["mirror"]


def test_the_degradation_reaches_a_human_on_stderr_in_human_mode(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex()
    index.fail_reads = True
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", providers=_providers(store, index=index)) == 0
    assert "device_index_unavailable" in capsys.readouterr().err


def test_the_degradation_reaches_stderr_in_dense_mode_without_touching_stdout(
    capsys: pytest.CaptureFixture[str],
):
    index = FakeHandwrittenTextIndex()
    index.fail_reads = True
    store = _mirror(texts=((_DOC, _PAGE_ONE, 0, _MIRROR_TEXT),))
    assert _search("retention", dense=True, providers=_providers(store, index=index)) == 0
    captured = capsys.readouterr()
    assert "device_index_unavailable" in captured.err
    assert captured.out.splitlines()[0] == "\t".join(DENSE_COLUMNS)


# ------------------------------- the excerpt, directly -------------------------------


def test_a_short_reading_is_shown_whole_with_whitespace_collapsed():
    assert _excerpt("two\nlines  here", "lines") == "two lines here"


def test_a_long_reading_is_cut_at_both_ends_when_the_term_is_in_the_middle():
    text = f"{'a' * 400} needle {'b' * 400}"
    excerpt = _excerpt(text, "needle")
    assert excerpt.startswith("...")
    assert excerpt.endswith("...")
    assert "needle" in excerpt
    assert len(excerpt) == EXCERPT_WIDTH + 6


def test_a_term_at_the_head_is_not_preceded_by_an_ellipsis():
    excerpt = _excerpt(f"needle {'b' * 400}", "needle")
    assert excerpt.startswith("needle")
    assert excerpt.endswith("...")


def test_a_term_at_the_tail_is_not_followed_by_an_ellipsis():
    excerpt = _excerpt(f"{'a' * 400} needle", "needle")
    assert excerpt.startswith("...")
    assert excerpt.endswith("needle")


def test_a_term_the_collapsed_reading_cannot_show_falls_back_to_the_head():
    text = "a" * 400
    excerpt = _excerpt(text, "needle")
    assert excerpt == f"{'a' * EXCERPT_WIDTH}..."
