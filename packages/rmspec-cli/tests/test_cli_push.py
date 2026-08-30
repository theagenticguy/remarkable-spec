"""``rmspec push``: the real page count, the refusals that precede the wire, and the envelope.

Nothing here issues ``POST /upload``. That route creates a document, the firmware's route table
is closed at six families and none of them deletes, so a test that reached the wire would leave
entries in the developer's own library that only a manual delete on the tablet removes.
:class:`~rmspec.device.testing.InMemoryDocumentUploader` is bound over ``DocumentUploader`` with
``override=True`` instead, and it records every request it was handed -- which is also what
makes "refused before anything was written" an assertion rather than a hope.
"""

from __future__ import annotations

import json as json_module
import os
import zipfile
from typing import TYPE_CHECKING, Any

import pytest
from dishka import Provider, Scope, provide

from rmspec.cli import _push
from rmspec.cli._container import FEATURE_MODULES, Feature
from rmspec.cli._invoke import FEATURE_MARKDOWN_PDF, FEATURE_PDF_READ
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog, InMemoryDocumentUploader
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    DocumentUploader,
    LibraryRefresh,
)
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import PdfPageReader
from rmspec.domain.ports.persistence import SyncAuditLog
from rmspec.persistence.testing import InMemorySyncAuditLog

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from rmspec.cli._invoke import Invoked
    from rmspec.domain.ports.export import PdfPageBackground, PdfSourceRef, PixelSize

_OVERRIDDEN_PORTS = (DocumentUploader, DeviceCatalog, SyncAuditLog, DependencyProbe, PdfPageReader)
"""The ports the doubles below bind, listed to give every import a runtime use.

dishka reads a provider's return annotation at container build to learn what it provides, so
none of these names may move into an ``if TYPE_CHECKING:`` block -- and this repo allows
neither a ``noqa`` nor a ``type: ignore``. Same discipline as ``_container.BOUND_PORTS``.
"""

_USAGE_STATUS = 2
"""What a ``UsageError`` exits with, restated so a refusal test reads without a lookup."""

_CONFIG_STATUS = 78
"""``EX_CONFIG``, which the domain's table gives both ``DeviceOperationUnsupported`` and
``MissingDependencyError``: in each case the command line or the environment is wrong rather
than the device."""

_ARCHIVE_UUID = "c" * 8
"""The document uuid an ``.rmdoc`` fixture names its own members after."""

_MARKDOWN = "# Notes\n\nOne paragraph a human can read on the tablet.\n"


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test here independent of the developer's own shell.

    ``load_settings`` reads the real environment, so an exported ``RMSPEC_*`` would change what
    a test measures or fail it outright. The loader variable is pinned rather than deleted
    because ``_markdown.py`` imports ``weasyprint``, which needs it on macOS -- and pinning it
    also stops ``apply_native_library_path`` mutating the interpreter these tests run in.
    """
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


class _PdfReaderDouble:
    """A :class:`~rmspec.domain.ports.export.PdfPageReader` answering one fixed page count.

    The real reader needs ``fitz``. Binding this instead keeps the pushed-a-PDF path testable
    without making the ``render`` extra a condition of running this file, and it records the
    reference it was handed so a test can prove the registry minted one.
    """

    def __init__(self, *, pages: int) -> None:
        self.pages = pages
        self.asked: list[PdfSourceRef] = []
        """Every reference this reader was asked about, in order."""

    def page_count(self, source: PdfSourceRef) -> int:
        self.asked.append(source)
        return self.pages

    def page_texts(self, source: PdfSourceRef) -> tuple[str, ...]:
        raise NotImplementedError

    def rasterize_page(
        self,
        source: PdfSourceRef,
        *,
        page_index: int,
        box: PixelSize,
        oversample: int,
    ) -> PdfPageBackground:
        raise NotImplementedError


class _ProbeDouble:
    """A :class:`~rmspec.domain.ports.errors.DependencyProbe` answering from one table."""

    def __init__(self, *, absent: frozenset[str] = frozenset()) -> None:
        self._absent = absent
        self.asked: list[str] = []
        """Every module name the probe was asked about, so a test can assert it probed none."""

    def is_installed(self, module_name: str, /) -> bool:
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, _module_name: str, /) -> str | None:
        return None


class _RequestDoubles(Provider):
    """Bind the two request-scoped device ports over the real transport bindings."""

    scope = Scope.REQUEST

    def __init__(
        self,
        uploader: InMemoryDocumentUploader,
        catalog: InMemoryDeviceCatalog,
    ) -> None:
        super().__init__()
        self._uploader = uploader
        self._catalog = catalog

    @provide(override=True)
    def document_uploader(self) -> DocumentUploader:
        return self._uploader

    @provide(override=True)
    def device_catalog(self) -> DeviceCatalog:
        return self._catalog


class _AppDoubles(Provider):
    """Bind the three app-scoped ports whose real bindings touch SQLite or a native library."""

    scope = Scope.APP

    def __init__(
        self,
        audit: InMemorySyncAuditLog,
        probe: _ProbeDouble,
        reader: _PdfReaderDouble,
    ) -> None:
        super().__init__()
        self._audit = audit
        self._probe = probe
        self._reader = reader

    @provide(override=True)
    def sync_audit_log(self) -> SyncAuditLog:
        return self._audit

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        return self._probe

    @provide(override=True)
    def pdf_page_reader(self) -> PdfPageReader:
        return self._reader


class _Rig:
    """One push run's doubles, bound and readable afterwards."""

    def __init__(
        self,
        *,
        uploader: InMemoryDocumentUploader,
        catalog: InMemoryDeviceCatalog,
        audit: InMemorySyncAuditLog,
        probe: _ProbeDouble,
        reader: _PdfReaderDouble,
    ) -> None:
        self.uploader = uploader
        self.catalog = catalog
        self.audit = audit
        self.probe = probe
        self.reader = reader

    @property
    def pages_recorded(self) -> int:
        """The ``pages_affected`` of the one history entry the run appended.

        Returns
        -------
        int
            What the command told the audit log, which is the page count it passed to the use
            case. This is how a placeholder would be caught: the request's own ``page_count``
            is not on the result, and history is where it lands.
        """
        return self.audit.recent(limit=1)[0].entry.pages_affected


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Rig]:
    """Return a factory that binds doubles into ``push`` and hands them back.

    ``push`` takes no ``providers=`` argument -- a real command never does -- so the factory
    wraps the ``run`` the module imported, appending the doubles to whatever it was passed.
    That keeps every test going through the real ``push`` signature, flags included.

    Returns
    -------
    Callable[..., _Rig]
        Keyword-only factory over the doubles' own options.
    """
    real_run = _push.run

    def build(
        *,
        documents: Sequence[DeviceDocument] = (),
        pdf_pages: int = 1,
        absent: frozenset[str] = frozenset(),
        honours_parent: bool = False,
        doc_uuid: str | None = None,
        refresh: LibraryRefresh = LibraryRefresh.ALREADY_VISIBLE,
    ) -> _Rig:
        built = _Rig(
            uploader=InMemoryDocumentUploader(
                refresh=refresh,
                doc_uuid=doc_uuid,
                honours_parent=honours_parent,
            ),
            catalog=InMemoryDeviceCatalog(documents=documents),
            audit=InMemorySyncAuditLog(),
            probe=_ProbeDouble(absent=absent),
            reader=_PdfReaderDouble(pages=pdf_pages),
        )
        doubles = (
            _RequestDoubles(built.uploader, built.catalog),
            _AppDoubles(built.audit, built.probe, built.reader),
        )

        def patched(
            body: Callable[[Invoked], int],
            /,
            *,
            json: bool = False,
            dense: bool = False,
            providers: Sequence[Provider] = (),
        ) -> int:
            return real_run(body, json=json, dense=dense, providers=(*providers, *doubles))

        monkeypatch.setattr(_push, "run", patched)
        return built

    return build


def _document(name: str) -> DeviceDocument:
    return DeviceDocument(
        uuid="d" * 8,
        name=name,
        file_type=DeviceFileType.PDF,
        page_count=1,
    )


def _markdown_file(tmp_path: Path, *, name: str = "notes.md", text: str = _MARKDOWN) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _pdf_file(tmp_path: Path, *, name: str = "report.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\ntrailer\n%%EOF\n")
    return path


def _rmdoc_file(tmp_path: Path, *, pages: object = None, name: str = "book.rmdoc") -> Path:
    listing = [{"id": f"p-{index}"} for index in range(3)] if pages is None else pages
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{_ARCHIVE_UUID}.metadata", json_module.dumps({"visibleName": "Book"}))
        archive.writestr(
            f"{_ARCHIVE_UUID}.content",
            json_module.dumps({"fileType": "notebook", "cPages": {"pages": listing}}),
        )
    return path


def _envelope(captured: str) -> dict[str, Any]:
    document: dict[str, Any] = json_module.loads(captured)
    return document


def _data(captured: str) -> dict[str, Any]:
    return _envelope(captured)["data"]


def _error(captured: str) -> dict[str, Any]:
    return _envelope(captured)["error"]


# ─────────────────────────── the discriminator and the feature table ───────────────────────────


def test_the_discriminator_comes_from_the_manifests_own_table():
    # Never a retyped string: the envelope and the manifest cannot be allowed to drift.
    assert RESPONSE_TYPES["push"] == "created"


def test_the_mirrored_feature_name_is_the_containers_own():
    assert Feature.MARKDOWN_PDF.value == FEATURE_MARKDOWN_PDF


def test_the_push_extra_names_both_of_its_modules_with_honest_native_flags():
    # weasyprint resolves as installed and then dies in dlopen when the loader path is unset --
    # the cairocffi failure with a different library name -- so a spec-only check would report
    # the feature available and fail mid-command. markdown has no native half.
    entry = {
        (module.package, module.extra, module.native)
        for module in FEATURE_MODULES[Feature.MARKDOWN_PDF]
    }

    assert entry == {("markdown", "push", False), ("weasyprint", "push", True)}


def test_the_container_still_mints_pdf_source_references_under_the_name_this_module_uses():
    # _REGISTRY_ATTRIBUTE is not part of _container.__all__, so a rename would otherwise turn
    # `rmspec push doc.pdf` into an AttributeError at the moment of use.
    container = __import__("importlib").import_module("rmspec.cli._container")

    assert hasattr(container, _push._REGISTRY_ATTRIBUTE)


def test_the_accepted_suffixes_are_the_ones_the_refusal_lists():
    assert _push.ACCEPTED_SUFFIXES == (".md", ".markdown", ".pdf", ".rmdoc")


# ─────────────────────────────── pushing authored Markdown ───────────────────────────────


def test_markdown_is_converted_to_a_pdf_and_uploaded_as_one(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    status = _push.push(_markdown_file(tmp_path), json=True)

    document = _envelope(capsys.readouterr().out)
    assert status == 0
    assert document["type"] == "created"
    assert document["degradations"] == []
    assert document["next"] == {
        "command": "rmspec ls --json",
        "purpose": "read the new document's uuid, which the upload route does not report",
    }
    data = document["data"]
    assert data["requested_name"] == "notes.pdf"
    assert data["visible_name"] == "notes.pdf"
    assert data["media"] == "pdf"
    assert data["doc_uuid"] is None
    assert built.uploader.uploaded[0].data.startswith(b"%PDF-")


def test_the_page_count_passed_on_is_the_renderers_and_not_a_placeholder(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The prior-art defect this guards: a neighbouring project uploads a valid, empty PDF
    # whenever its own parser silently returns nothing. A literal 1 would satisfy the zero-page
    # refusal while telling the history log something false, so the count has to move with the
    # content.
    long_source = "\n\n".join(f"## Section {index}\n\nParagraph {index}." for index in range(200))
    built = rig()

    _push.push(_markdown_file(tmp_path, text=long_source), json=True)

    capsys.readouterr()
    assert built.pages_recorded > 1


def test_a_short_document_records_the_one_page_it_has(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    _push.push(_markdown_file(tmp_path), json=True)

    capsys.readouterr()
    assert built.pages_recorded == 1


def test_a_blank_markdown_source_is_refused_before_the_wire(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # weasyprint lays out one empty page for empty input, so page_count == 0 can never catch
    # this. The source is checked instead, and nothing reaches the uploader.
    built = rig()

    status = _push.push(_markdown_file(tmp_path, text="   \n\n\t\n"), json=True)

    document = _envelope(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert document["type"] == "error"
    assert built.uploader.upload_calls == 0


def test_markdown_that_is_not_utf8_is_refused_rather_than_decoded_with_replacements(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()
    path = tmp_path / "latin.md"
    path.write_bytes(b"# caf\xe9\n")

    status = _push.push(path, json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert error["type"] == "UsageError"
    assert "UTF-8" in str(error["message"])
    assert built.uploader.upload_calls == 0


def test_a_missing_push_extra_fails_before_the_file_is_even_read(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The legacy defect turned into an assertion: 27 function-local import sites raised
    # ImportError, every one reached only after the user had already paid for something. The
    # path here does not exist, so a probe that ran second would report the wrong failure.
    built = rig(absent=frozenset({"weasyprint"}))

    status = _push.push(tmp_path / "absent.md", json=True)

    error = _error(capsys.readouterr().out)
    assert error["type"] == "MissingDependencyError"
    assert error["remediation"] == "uv sync --extra push"
    assert status == _CONFIG_STATUS
    assert built.uploader.upload_calls == 0


def test_both_missing_modules_are_learned_from_one_run(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    rig(absent=frozenset({"markdown", "weasyprint"}))

    _push.push(_markdown_file(tmp_path), json=True)

    captured = capsys.readouterr()
    assert "weasyprint" in captured.err
    assert "uv sync --extra push" in captured.err


# ─────────────────────────────── pushing a PDF as it is ───────────────────────────────


def test_a_pdf_keeps_its_own_name_and_is_counted_through_the_reader_port(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The multipart filename becomes the visible name verbatim, extension included, so the
    # default name is the file's own name and nothing is appended to it.
    built = rig(pdf_pages=7)

    status = _push.push(_pdf_file(tmp_path), json=True)

    data = _data(capsys.readouterr().out)
    assert status == 0
    assert data["requested_name"] == "report.pdf"
    assert data["visible_name"] == "report.pdf"
    assert built.pages_recorded == 7
    assert len(built.reader.asked) == 1


def test_the_pdf_path_probes_the_reader_and_not_the_markdown_extra(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    _push.push(_pdf_file(tmp_path), json=True)

    capsys.readouterr()
    assert built.probe.asked == [module.package for module in FEATURE_MODULES[Feature.PDF_READ]]
    assert Feature.PDF_READ.value == FEATURE_PDF_READ


# ─────────────────────────────── pushing an .rmdoc archive ───────────────────────────────


def test_an_archive_is_counted_from_its_own_page_order(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # Not the count of .rm members: measured, an archive can carry 16 scene members for 10
    # pages because layers orphaned by an edit stay in the store, unreachable from cPages.
    built = rig()

    status = _push.push(_rmdoc_file(tmp_path), json=True)

    data = _data(capsys.readouterr().out)
    assert status == 0
    assert data["media"] == "rmdoc"
    assert data["visible_name"] is None
    assert built.pages_recorded == 3


def test_an_archive_never_consults_the_catalog_because_it_names_itself(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The archive's own metadata name wins over the multipart filename, so refusing on the
    # requested name would refuse the wrong string.
    built = rig(documents=(_document("book.rmdoc"),))

    status = _push.push(_rmdoc_file(tmp_path), json=True)

    capsys.readouterr()
    assert status == 0
    assert built.catalog.list_calls == 0


def test_bytes_that_are_not_a_zip_are_refused_as_a_usage_error(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()
    path = tmp_path / "broken.rmdoc"
    path.write_bytes(b"not a zip at all")

    status = _push.push(path, json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert error["type"] == "UsageError"
    assert built.uploader.upload_calls == 0


def test_an_archive_whose_page_list_is_not_a_list_is_refused(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    status = _push.push(_rmdoc_file(tmp_path, pages={"id": "one"}), json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert "not a list" in str(error["message"])
    assert built.uploader.upload_calls == 0


def test_an_archive_with_no_pages_is_refused_by_the_use_case(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The real count is zero and it is passed on honestly; CreateDocument is what refuses it.
    built = rig()

    status = _push.push(_rmdoc_file(tmp_path, pages=[]), json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert "0 pages" in str(error["message"])
    assert built.uploader.upload_calls == 0


# ─────────────────────────────── what is refused from the suffix ───────────────────────────────


def test_an_epub_is_refused_rather_than_given_a_guessed_page_count(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The route accepts one and UploadMedia has a member for it, but a reflowable book has no
    # page count until a reader lays it out, and a placeholder is what the zero-page refusal
    # exists to catch. The refusal names the accepted set, so the gap is a sentence.
    built = rig()
    path = tmp_path / "book.epub"
    path.write_bytes(b"PK\x03\x04")

    status = _push.push(path, json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert ".rmdoc" in str(error["message"])
    assert built.uploader.upload_calls == 0


@pytest.mark.parametrize("name", ["notes.txt", "notes"])
def test_a_suffix_this_command_does_not_accept_costs_no_read(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
):
    built = rig()

    status = _push.push(tmp_path / name, json=True)

    capsys.readouterr()
    assert status == _USAGE_STATUS
    assert built.probe.asked == []
    assert built.uploader.upload_calls == 0


def test_the_suffix_is_read_case_insensitively(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    status = _push.push(_markdown_file(tmp_path, name="NOTES.MD"), json=True)

    capsys.readouterr()
    assert status == 0
    assert built.uploader.uploaded[0].name == "NOTES.pdf"


def test_a_path_that_is_not_a_file_is_refused(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    status = _push.push(tmp_path / "gone.pdf", json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert "not a file" in str(error["message"])
    assert built.uploader.upload_calls == 0


# ─────────────────────────────── --parent, --name, duplicates ───────────────────────────────


def test_a_parent_a_transport_cannot_honour_raises_instead_of_landing_at_the_root(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The USB import route has no destination parameter, and it targets whatever folder was
    # listed last -- device-global mutable traversal state, which is a race rather than an API.
    # So a caller who asked for a folder is told no, never handed the root with a 201.
    built = rig(honours_parent=False)

    status = _push.push(_pdf_file(tmp_path), parent="f" * 8, json=True)

    error = _error(capsys.readouterr().out)
    assert status == _CONFIG_STATUS
    assert error["type"] == "DeviceOperationUnsupported"
    assert built.uploader.uploaded == []


def test_a_parent_reaches_the_transport_verbatim_when_it_can_honour_one(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # Whether --parent works is a fact about RMSPEC_TRANSPORT, decided by the binding. This
    # module neither rewrites the value nor drops it.
    built = rig(honours_parent=True)

    status = _push.push(_pdf_file(tmp_path), parent="f" * 8, json=True)

    capsys.readouterr()
    assert status == 0
    assert built.uploader.uploaded[0].parent_uuid == "f" * 8


def test_an_explicit_name_replaces_the_files_own(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    status = _push.push(_markdown_file(tmp_path), name="Reading list.pdf", json=True)

    capsys.readouterr()
    assert status == 0
    assert built.uploader.uploaded[0].name == "Reading list.pdf"


def test_a_name_the_library_root_already_holds_is_refused_by_default(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # Created is irreversible over this route, so a duplicate costs a manual delete on the
    # tablet and the name has to be right before the call rather than fixable after it.
    built = rig(documents=(_document("notes.pdf"),))

    status = _push.push(_markdown_file(tmp_path), json=True)

    error = _error(capsys.readouterr().out)
    assert status == _USAGE_STATUS
    assert error["type"] == "UsageError"
    assert built.uploader.upload_calls == 0


def test_opting_in_to_a_duplicate_creates_it_and_says_what_that_costs(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig(documents=(_document("notes.pdf"),))

    status = _push.push(_markdown_file(tmp_path), allow_duplicate_name=True, dense=True)

    captured = capsys.readouterr()
    assert status == 0
    assert built.uploader.upload_calls == 1
    assert "already carried 'notes.pdf'" in captured.err
    assert "manual delete on the tablet" in captured.err


def test_the_duplicate_warning_is_absent_when_the_name_was_free(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    rig()

    _push.push(_markdown_file(tmp_path), dense=True)

    assert "already carried" not in capsys.readouterr().err


# ─────────────────────────────── the dense projection ───────────────────────────────


def test_dense_writes_the_columns_and_one_record(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    built = rig(doc_uuid="e" * 8, refresh=LibraryRefresh.VISIBILITY_FORCED)

    status = _push.push(_markdown_file(tmp_path), dense=True)

    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert lines[0].split("\t") == list(_push.PUSH_COLUMNS)
    assert lines[1].split("\t") == [
        "e" * 8,
        "notes.pdf",
        "notes.pdf",
        "pdf",
        str(len(built.uploader.uploaded[0].data)),
        "visibility_forced",
    ]


def test_an_unknown_identity_is_an_empty_cell_and_never_the_string_none(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # doc_uuid is None over USB -- the 201 body carries no id and this project refuses to guess
    # one by re-listing -- and visible_name is None for an .rmdoc, whose own metadata name wins.
    rig()

    _push.push(_rmdoc_file(tmp_path), dense=True)

    cells = capsys.readouterr().out.splitlines()[1].split("\t")
    assert cells[0] == ""
    assert cells[2] == ""
    assert "None" not in cells


# ─────────────────────────────── the human receipt ───────────────────────────────


def test_the_default_mode_writes_nothing_to_stdout_and_puts_its_receipt_on_stderr(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # The rule this test exists for: stdout carries the machine-consumable payload and nothing
    # else. A default invocation that wrote a tab-separated record there made `2>/dev/null` a lie
    # and `| jq` a parse error, and no test caught it.
    built = rig(doc_uuid="e" * 8, refresh=LibraryRefresh.VISIBILITY_FORCED)

    status = _push.push(_markdown_file(tmp_path))

    captured = capsys.readouterr()
    assert status == 0
    assert built.uploader.upload_calls == 1
    assert captured.out == ""
    assert "\t" not in captured.err
    # Every fact the dense record carries, rotated into a labelled line -- nothing is dropped.
    for label in _push.PUSH_COLUMNS:
        assert label in captured.err
    assert "e" * 8 in captured.err
    assert "notes.pdf" in captured.err
    assert "visibility_forced" in captured.err
    assert _push.UNREPORTED not in captured.err


def test_a_cell_the_run_has_no_value_for_says_so_rather_than_dangling(
    rig: Callable[..., _Rig],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    # doc_uuid is None over USB and visible_name is None for an .rmdoc, whose own metadata name
    # wins. --dense leaves both cells empty and must; a person reading a label with nothing after
    # it reads a bug, so the human rendering says it in words.
    rig()

    status = _push.push(_rmdoc_file(tmp_path))

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == ""
    assert captured.err.count(_push.UNREPORTED) == 2
    assert "rmdoc" in captured.err
