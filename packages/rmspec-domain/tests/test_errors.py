"""Behavioural tests for the error tree, its closed enums, and the exit-code table.

Pure in-process assertions: no filesystem, no network, no device, no subprocess. What is
under test is the contract the other layers lean on -- that ``except RmspecError`` is the
one broad catch the architecture needs, that every failure in the tree resolves to exactly
one process status, that the statuses shared by more than one branch are the reviewed ones
rather than accidents, and that :class:`MissingDependencyError` carries the package, the
extra and the feature a user needs in order to fix a wiring bug (defect 4).

The expected class-to-status table below is written out by hand rather than derived from
``_EXIT_CODES``, so a change to the table is a failing test and not a silently agreeing
tautology. The tree membership is likewise enumerated by hand: a new error class fails
``test_the_tree_is_exactly_the_enumerated_set`` until someone gives it a sample and a
status.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Final, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from rmspec.domain import errors as errors_module
from rmspec.domain.errors import (
    AllRecognizersFailed,
    AmbiguousDocument,
    ArtifactWriteFailed,
    ArtifactWriteReason,
    AuditWriteFailedError,
    BackgroundUnreadable,
    ConfigurationError,
    CorruptPageData,
    Degradation,
    DegradationKind,
    DeviceAuthFailed,
    DeviceDocumentNotFound,
    DeviceError,
    DeviceOperationUnsupported,
    DeviceProtocolError,
    DeviceTransferInterrupted,
    DeviceUnreachable,
    DeviceUploadRejected,
    DocumentCandidate,
    DocumentNotFound,
    DocumentSourceError,
    DocumentStoreUnavailable,
    ExportError,
    FormatError,
    InvalidSettingError,
    MalformedDeviceMetadata,
    MalformedDocument,
    MissingDependencyError,
    ModelAccessDenied,
    ModelError,
    ModelRejectedRequest,
    ModelResponseMalformed,
    ModelThrottled,
    ModelUnavailable,
    NoTextRecognized,
    OcrError,
    PageNotFound,
    PdfCompositionFailed,
    PdfPageOutOfRange,
    PdfSourceUnreadable,
    PersistenceError,
    RasterizationFailed,
    RecognitionFailed,
    RenderError,
    RmspecError,
    StoredRecordUnreadableError,
    StoreSchemaMismatchError,
    StoreUnavailableError,
    TransportKind,
    UnsupportedPageFormat,
    UnsupportedPenType,
    UsageError,
    XochitlDirNotConfigured,
    exit_code,
)

if TYPE_CHECKING:
    from enum import StrEnum

# ─────────────────────────────── fixtures as plain data ───────────────────────────────

CANDIDATES: Final = (
    DocumentCandidate(uuid="1f0a-notes", name="Sprint notes"),
    DocumentCandidate(uuid="2b71-notes", name="Sprint notes (older)"),
)
"""Two candidates for the one error that has to show a human a choice."""

SAMPLES: Final[tuple[RmspecError, ...]] = (
    RmspecError("a root instance, which only a test ever builds"),
    MissingDependencyError(package="fitz", extra="export", feature="PDF export"),
    ConfigurationError("a base instance, which only a test ever builds"),
    XochitlDirNotConfigured(consulted=("--xochitl", "RMSPEC_XOCHITL", "~/.remarkable-spec")),
    InvalidSettingError(setting="render_dpi", value="0", requirement="a positive integer"),
    UsageError(subject="--strict with --force", requirement="at most one of them"),
    DocumentSourceError("a base instance, which only a test ever builds"),
    DocumentStoreUnavailable(store="local mirror", detail="the directory is gone"),
    DocumentNotFound(query="sprint", store="local mirror"),
    AmbiguousDocument(query="sprint", candidates=CANDIDATES),
    PageNotFound(document_uuid="1f0a-notes", page="7", page_count=4),
    FormatError("a base instance, which only a test ever builds"),
    MalformedDocument(document_uuid="1f0a-notes", artifact="metadata", detail="no visibleName"),
    CorruptPageData(page_uuid="9c1e-page", detail="truncated block", offset=512),
    UnsupportedPageFormat(
        page_uuid="9c1e-page",
        observed_version="7.0",
        supported_versions=("6.0",),
    ),
    RenderError("a base instance, which only a test ever builds"),
    UnsupportedPenType(pen="airbrush-2", page_ref="9c1e-page"),
    BackgroundUnreadable(page_ref="9c1e-page", detail="root element is not svg"),
    ExportError("a base instance, which only a test ever builds"),
    RasterizationFailed(backend="cairosvg", detail="zero bytes returned", page_ref="9c1e-page"),
    PdfCompositionFailed(expected_pages=4, detail="readback disagreed", actual_pages=1),
    PdfSourceUnreadable(source="paper.pdf", detail="needs a password"),
    PdfPageOutOfRange(source="paper.pdf", page_index=9, page_count=4),
    ArtifactWriteFailed(
        name="page-01.png",
        reason=ArtifactWriteReason.ALREADY_PRESENT,
        detail="policy forbids overwriting",
    ),
    DeviceError("a base instance, which only a test ever builds", transport=TransportKind.SSH),
    DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connect refused",
    ),
    DeviceAuthFailed(
        transport=TransportKind.SSH,
        user="root",
        detail="key rejected",
        key_source="~/.ssh/remarkable",
    ),
    DeviceProtocolError(
        transport=TransportKind.USB_WEB_API,
        route="/download/placeholder",
        expected="a body starting with %PDF-",
        got="text/html",
    ),
    DeviceDocumentNotFound(transport=TransportKind.USB_WEB_API, document_uuid="1f0a-notes"),
    MalformedDeviceMetadata(
        transport=TransportKind.SSH,
        detail="lastModified is not an integer",
        document_uuid="1f0a-notes",
    ),
    DeviceTransferInterrupted(
        transport=TransportKind.SSH,
        subject="1f0a-notes.rmdoc",
        bytes_transferred=1024,
        bytes_expected=8192,
    ),
    DeviceUploadRejected(
        transport=TransportKind.USB_WEB_API,
        name="brief.pdf",
        device_message="unsupported media",
    ),
    DeviceOperationUnsupported(
        transport=TransportKind.USB_WEB_API,
        operation="raw bundle download",
        supported_by=(TransportKind.SSH, TransportKind.LOCAL_MIRROR),
    ),
    OcrError("a base instance, which only a test ever builds"),
    RecognitionFailed(provider_id="textract", detail="throttled", retryable=True),
    AllRecognizersFailed(failures={"vision": "no handler", "textract": "throttled"}),
    NoTextRecognized(page_ref="9c1e-page", providers=("vision", "textract")),
    ModelError("a base instance, which only a test ever builds"),
    ModelUnavailable(
        endpoint="bedrock-runtime.us-west-2.amazonaws.com",
        detail="connect timeout after 10s",
        retryable=True,
    ),
    ModelAccessDenied(
        model_id="global.openai.gpt-5.6-luna",
        remediation="enable global.openai.gpt-5.6-luna in us-west-2 in the Bedrock console",
    ),
    ModelThrottled(model_id="global.anthropic.claude-opus-4-6-v1", retry_after_s=1.5),
    ModelRejectedRequest(model_id="global.anthropic.claude-opus-4-6-v1", detail="payload too big"),
    ModelResponseMalformed(
        model_id="global.anthropic.claude-opus-4-6-v1",
        detail="no text block",
    ),
    PersistenceError("a base instance, which only a test ever builds"),
    StoreUnavailableError(store="sync.db", detail="the parent cannot be created"),
    StoreSchemaMismatchError(store="sync.db", found=3, expected=5),
    StoredRecordUnreadableError(
        store="sync.db",
        table="ocr_cache",
        key="9c1e-page",
        detail="payload failed validation",
    ),
    AuditWriteFailedError(detail="the history table is locked"),
)
"""One live instance of every class in the tree, so no constructor goes unexercised."""

SAMPLE_BY_CLASS: Final = {type(err): err for err in SAMPLES}

EXPECTED_STATUS: Final[dict[type[RmspecError], int]] = {
    RmspecError: 1,
    MissingDependencyError: 78,
    ConfigurationError: 78,
    XochitlDirNotConfigured: 78,
    InvalidSettingError: 78,
    UsageError: 2,
    DocumentSourceError: 66,
    DocumentStoreUnavailable: 69,
    DocumentNotFound: 66,
    AmbiguousDocument: 2,
    PageNotFound: 66,
    FormatError: 65,
    MalformedDocument: 65,
    CorruptPageData: 65,
    UnsupportedPageFormat: 65,
    RenderError: 70,
    UnsupportedPenType: 70,
    BackgroundUnreadable: 70,
    ExportError: 73,
    RasterizationFailed: 73,
    PdfCompositionFailed: 73,
    PdfSourceUnreadable: 65,
    PdfPageOutOfRange: 66,
    ArtifactWriteFailed: 73,
    DeviceError: 69,
    DeviceUnreachable: 69,
    DeviceAuthFailed: 77,
    DeviceProtocolError: 69,
    DeviceDocumentNotFound: 66,
    MalformedDeviceMetadata: 65,
    DeviceTransferInterrupted: 69,
    DeviceUploadRejected: 69,
    DeviceOperationUnsupported: 78,
    OcrError: 69,
    RecognitionFailed: 69,
    AllRecognizersFailed: 69,
    NoTextRecognized: 69,
    ModelError: 69,
    ModelUnavailable: 69,
    ModelAccessDenied: 77,
    ModelThrottled: 75,
    ModelRejectedRequest: 65,
    ModelResponseMalformed: 65,
    PersistenceError: 74,
    StoreUnavailableError: 74,
    StoreSchemaMismatchError: 78,
    StoredRecordUnreadableError: 65,
    AuditWriteFailedError: 74,
}
"""The status every class must exit with, restated independently of the module's table."""

DECLARED_ROWS: Final[dict[int, frozenset[type[RmspecError]]]] = {
    1: frozenset({RmspecError}),
    2: frozenset({UsageError, AmbiguousDocument}),
    65: frozenset(
        {
            FormatError,
            PdfSourceUnreadable,
            MalformedDeviceMetadata,
            ModelRejectedRequest,
            ModelResponseMalformed,
            StoredRecordUnreadableError,
        }
    ),
    66: frozenset({DocumentSourceError, PdfPageOutOfRange, DeviceDocumentNotFound}),
    69: frozenset({DocumentStoreUnavailable, DeviceError, OcrError}),
    70: frozenset({RenderError}),
    73: frozenset({ExportError}),
    74: frozenset({PersistenceError}),
    75: frozenset({ModelThrottled}),
    77: frozenset({DeviceAuthFailed, ModelAccessDenied}),
    78: frozenset(
        {
            MissingDependencyError,
            ConfigurationError,
            DeviceOperationUnsupported,
            StoreSchemaMismatchError,
        }
    ),
}
"""Which classes are allowed to own a row, grouped by the status they claim.

Sharing a status is not a defect -- sharing one *by accident* is. Anything that appears
here was reviewed into place; anything that appears in the module's table and not here
fails ``test_only_declared_classes_own_a_row``.
"""

SLICE_BASES: Final = (
    ConfigurationError,
    DocumentSourceError,
    FormatError,
    RenderError,
    ExportError,
    DeviceError,
    OcrError,
    PersistenceError,
)
"""The bases documented as "never raised directly": the classes a use case catches."""

CARRIES_REMEDIATION: Final = frozenset(
    {
        MissingDependencyError,
        XochitlDirNotConfigured,
        InvalidSettingError,
        UsageError,
        AmbiguousDocument,
        DeviceUnreachable,
        DeviceOperationUnsupported,
        ModelUnavailable,
        ModelAccessDenied,
    }
)
"""The classes that offer a single obvious next action. Every other one offers none."""

CLOSED_ENUMS: Final = (TransportKind, ArtifactWriteReason, DegradationKind)

ALLOWED_STATUSES: Final = frozenset({1, 2, 65, 66, 69, 70, 73, 74, 75, 77, 78})
"""BSD ``sysexits`` values plus generic failure and usage. Nothing else may be returned."""

TEXT: Final = st.text(min_size=1, max_size=40)
"""Non-empty display strings: everything the tree carries is an opaque display string."""


class _OutsideTheTreeError(Exception):
    """An exception whose MRO holds no row at all, for the totality of :func:`exit_code`."""


def _raise_missing_dependency() -> None:
    """Raise a wiring bug from a helper, so the test's ``try`` holds only a call."""
    raise MissingDependencyError(package="fitz", extra="export", feature="PDF export")


def _descendants(root: type[RmspecError]) -> frozenset[type[RmspecError]]:
    """Return ``root`` and every class that inherits from it, transitively."""
    found: set[type[RmspecError]] = {root}
    for subclass in root.__subclasses__():
        found |= _descendants(subclass)
    return frozenset(found)


TREE: Final = _descendants(RmspecError)
"""Snapshot of the live tree at import, so subclasses defined inside tests cannot leak in."""

LEAVES: Final = frozenset(cls for cls in TREE if not cls.__subclasses__())


# ─────────────────────────────── the tree's shape ───────────────────────────────


def test_the_tree_is_exactly_the_enumerated_set() -> None:
    assert frozenset(EXPECTED_STATUS) == TREE


def test_every_class_in_the_tree_has_a_sample() -> None:
    assert set(SAMPLE_BY_CLASS) == set(TREE)


def test_no_two_samples_share_a_class() -> None:
    assert len(SAMPLE_BY_CLASS) == len(SAMPLES)


def test_the_tree_has_the_documented_size() -> None:
    assert len(TREE) == 48
    assert len(LEAVES) == 37


def test_exported_names_all_resolve() -> None:
    missing = [name for name in errors_module.__all__ if not hasattr(errors_module, name)]
    assert missing == []


def test_the_exported_exceptions_are_exactly_the_tree() -> None:
    exported = {
        obj
        for obj in (getattr(errors_module, name) for name in errors_module.__all__)
        if isinstance(obj, type) and issubclass(obj, BaseException)
    }
    assert exported == set(TREE)


def test_every_error_is_an_ordinary_exception_not_a_base_exception() -> None:
    non_exceptions = [cls.__name__ for cls in TREE if not issubclass(cls, Exception)]
    assert non_exceptions == []


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_one_broad_catch_covers_the_whole_tree(sample: RmspecError) -> None:
    with pytest.raises(RmspecError):
        raise sample


def test_document_source_and_format_branches_are_disjoint() -> None:
    both = [
        cls.__name__
        for cls in TREE
        if issubclass(cls, DocumentSourceError) and issubclass(cls, FormatError)
    ]
    assert both == []


def test_a_missing_document_is_not_a_device_protocol_violation() -> None:
    assert not issubclass(DeviceDocumentNotFound, DeviceProtocolError)
    assert issubclass(DeviceDocumentNotFound, DeviceError)


def test_catching_store_unavailable_still_catches_a_schema_mismatch() -> None:
    mismatch = StoreSchemaMismatchError(store="sync.db", found=3, expected=5)
    assert isinstance(mismatch, StoreUnavailableError)
    assert isinstance(mismatch, PersistenceError)


def test_model_failures_are_caught_by_the_ocr_slice_base() -> None:
    # Five children, not four: `ModelUnavailable` is what stops an adapter reporting a
    # permanently dead endpoint as a throttle, or letting `httpx.ConnectError` cross the port.
    model_classes = [cls for cls in TREE if issubclass(cls, ModelError)]
    assert len(model_classes) == 6
    assert all(issubclass(cls, OcrError) for cls in model_classes)


def test_slice_bases_are_direct_children_of_the_root() -> None:
    assert all(base.__bases__ == (RmspecError,) for base in SLICE_BASES)


def test_slice_bases_do_not_assemble_their_own_message() -> None:
    without_own_init = [base for base in SLICE_BASES if "__init__" not in vars(base)]
    assert set(without_own_init) == set(SLICE_BASES) - {DeviceError}


# ─────────────────────────── the missing-dependency contract ───────────────────────────


def test_missing_dependency_carries_package_extra_and_feature() -> None:
    err = MissingDependencyError(package="Quartz", extra="ocr", feature="Apple Vision OCR")
    assert err.package == "Quartz"
    assert err.extra == "ocr"
    assert err.feature == "Apple Vision OCR"


def test_missing_dependency_names_the_import_and_the_installable_extra() -> None:
    err = MissingDependencyError(package="fitz", extra="export", feature="PDF export")
    assert "fitz" in err.message
    assert "PDF export" in err.message
    assert err.remediation == "uv sync --extra export"


@given(package=TEXT, extra=TEXT, feature=TEXT)
def test_missing_dependency_always_shows_both_names(
    package: str,
    extra: str,
    feature: str,
) -> None:
    err = MissingDependencyError(package=package, extra=extra, feature=feature)
    assert package in err.message
    assert feature in err.message
    assert err.remediation is not None
    assert extra in err.remediation
    assert (err.package, err.extra, err.feature) == (package, extra, feature)


def test_a_distribution_name_may_differ_from_the_import_name() -> None:
    err = MissingDependencyError(package="fitz", extra="export", feature="PDF export")
    assert "pymupdf" not in err.message
    assert err.package != err.extra


@pytest.mark.parametrize("base", SLICE_BASES, ids=lambda cls: cls.__name__)
def test_missing_dependency_hangs_off_no_slice_base(base: type[RmspecError]) -> None:
    assert not issubclass(MissingDependencyError, base)


def test_missing_dependency_is_a_direct_child_of_the_root() -> None:
    assert MissingDependencyError.__bases__ == (RmspecError,)


def test_except_export_error_cannot_swallow_a_wiring_bug() -> None:
    caught: RmspecError | None = None
    try:
        _raise_missing_dependency()
    except ExportError as exc:  # pragma: no cover - the point is that this never runs
        caught = exc
    except RmspecError as exc:
        caught = exc
    assert isinstance(caught, MissingDependencyError)


def test_a_wiring_bug_exits_as_configuration_not_as_an_export_failure() -> None:
    wiring = MissingDependencyError(package="cairocffi", extra="render", feature="PNG export")
    export = RasterizationFailed(backend="cairosvg", detail="zero bytes returned")
    assert exit_code(wiring) == 78
    assert exit_code(export) != exit_code(wiring)


# ─────────────────────────────── the exit-code table ───────────────────────────────


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_every_error_maps_to_its_declared_status(sample: RmspecError) -> None:
    assert exit_code(sample) == EXPECTED_STATUS[type(sample)]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_a_status_is_a_single_value_per_class(sample: RmspecError) -> None:
    other = SAMPLE_BY_CLASS[type(sample)]
    assert exit_code(sample) == exit_code(other) == exit_code(sample)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_no_error_ever_exits_as_success(sample: RmspecError) -> None:
    assert exit_code(sample) != 0


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_statuses_stay_inside_the_allowed_set(sample: RmspecError) -> None:
    assert exit_code(sample) in ALLOWED_STATUSES


def test_only_the_root_falls_through_to_generic_failure() -> None:
    generic = {cls.__name__ for cls, err in SAMPLE_BY_CLASS.items() if exit_code(err) == 1}
    assert generic == {"RmspecError"}


def test_a_class_without_a_row_exits_exactly_as_its_nearest_ancestor() -> None:
    for cls, err in SAMPLE_BY_CLASS.items():
        if cls in errors_module._EXIT_CODES:
            continue
        ancestors = [base for base in cls.__mro__[1:] if issubclass(base, RmspecError)]
        nearest = next(base for base in ancestors if base in SAMPLE_BY_CLASS)
        assert exit_code(err) == exit_code(SAMPLE_BY_CLASS[nearest]), cls.__name__


def test_only_declared_classes_own_a_row() -> None:
    rows: dict[int, set[type[RmspecError]]] = {}
    for cls, status in errors_module._EXIT_CODES.items():
        rows.setdefault(status, set()).add(cls)
    assert {status: frozenset(group) for status, group in rows.items()} == DECLARED_ROWS


def test_sharing_a_status_never_collapses_machine_identity() -> None:
    for group in DECLARED_ROWS.values():
        codes = {SAMPLE_BY_CLASS[cls].code for cls in group}
        assert len(codes) == len(group)


def test_top_level_branches_collide_only_where_reviewed() -> None:
    branches = [cls for cls in TREE if cls.__bases__ == (RmspecError,)]
    by_status: dict[int, set[str]] = {}
    for cls in branches:
        by_status.setdefault(exit_code(SAMPLE_BY_CLASS[cls]), set()).add(cls.__name__)
    shared = {status: names for status, names in by_status.items() if len(names) > 1}
    assert shared == {
        69: {"DeviceError", "OcrError"},
        78: {"MissingDependencyError", "ConfigurationError"},
    }


def test_every_leaf_resolves_without_consulting_the_root_row() -> None:
    for cls in LEAVES:
        rows = [base for base in cls.__mro__ if base in errors_module._EXIT_CODES]
        assert rows[0] is not RmspecError, cls.__name__


def test_exit_code_is_total_for_a_type_with_no_row_at_all() -> None:
    outsider = cast("RmspecError", _OutsideTheTreeError("not in the tree"))
    assert exit_code(outsider) == 1


def test_exit_code_does_not_mutate_the_table() -> None:
    before = dict(errors_module._EXIT_CODES)
    for sample in SAMPLES:
        exit_code(sample)
    assert dict(errors_module._EXIT_CODES) == before


def test_the_table_holds_only_error_classes() -> None:
    strays = [cls for cls in errors_module._EXIT_CODES if not issubclass(cls, RmspecError)]
    assert strays == []


# ─────────────────────────── message and remediation contract ───────────────────────────


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_the_message_is_assembled_and_is_the_string_form(sample: RmspecError) -> None:
    assert sample.message
    assert str(sample) == sample.message
    assert sample.args == (sample.message,)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_no_message_leaks_a_placeholder_or_an_absent_optional(sample: RmspecError) -> None:
    assert "None" not in sample.message
    assert "{" not in sample.message


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_only_the_documented_classes_offer_a_remediation(sample: RmspecError) -> None:
    offered = sample.remediation is not None
    assert offered is (type(sample) in CARRIES_REMEDIATION)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_a_remediation_is_never_blank_when_present(sample: RmspecError) -> None:
    assert sample.remediation is None or sample.remediation.strip()


def test_the_root_takes_an_optional_remediation() -> None:
    bare = RmspecError("a failure with no obvious next step")
    guided = RmspecError("a failure with one", remediation="try the other transport")
    assert bare.remediation is None
    assert guided.remediation == "try the other transport"


# ─────────────────────────────── the machine-readable code ───────────────────────────────


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda err: type(err).__name__)
def test_the_code_is_the_class_name(sample: RmspecError) -> None:
    assert sample.code == type(sample).__name__


def test_codes_are_unique_across_the_whole_tree() -> None:
    codes = [err.code for err in SAMPLES]
    assert len(set(codes)) == len(codes)


def test_the_code_is_derived_so_it_cannot_drift_from_the_class() -> None:
    class LaterAddedError(ExportError):
        """A subclass invented at runtime, to prove ``code`` is not a stored literal."""

    assert LaterAddedError("added later").code == "LaterAddedError"
    assert exit_code(LaterAddedError("added later")) == exit_code(SAMPLE_BY_CLASS[ExportError])


# ─────────────────────────────── optional fields, both ways ───────────────────────────────


def test_page_not_found_mentions_the_page_count_only_when_it_has_one() -> None:
    with_count = PageNotFound(document_uuid="1f0a", page="7", page_count=4)
    without = PageNotFound(document_uuid="1f0a", page="7")
    assert "of 4" in with_count.message
    assert with_count.page_count == 4
    assert " of " not in without.message
    assert without.page_count is None


def test_corrupt_page_data_mentions_the_byte_offset_only_when_it_has_one() -> None:
    with_offset = CorruptPageData(page_uuid="9c1e", detail="truncated", offset=0)
    without = CorruptPageData(page_uuid="9c1e", detail="truncated")
    assert "at byte 0" in with_offset.message
    assert with_offset.offset == 0
    assert "byte" not in without.message


def test_rasterization_names_the_page_or_the_markup() -> None:
    for_page = RasterizationFailed(backend="cairosvg", detail="crash", page_ref="9c1e")
    for_markup = RasterizationFailed(backend="cairosvg", detail="crash")
    assert "page 9c1e" in for_page.message
    assert "markup" in for_markup.message
    assert for_markup.page_ref is None


def test_pdf_composition_reports_the_count_it_actually_produced() -> None:
    mismatch = PdfCompositionFailed(expected_pages=4, detail="readback", actual_pages=1)
    unknown = PdfCompositionFailed(expected_pages=4, detail="readback")
    assert "4-page" in mismatch.message
    assert "composed 1" in mismatch.message
    assert "composed" not in unknown.message
    assert unknown.actual_pages is None


def test_device_auth_names_the_key_source_only_when_one_was_offered() -> None:
    with_key = DeviceAuthFailed(
        transport=TransportKind.SSH,
        user="root",
        detail="rejected",
        key_source="~/.ssh/remarkable",
    )
    without = DeviceAuthFailed(transport=TransportKind.SSH, user="root", detail="rejected")
    assert "using ~/.ssh/remarkable" in with_key.message
    assert "using" not in without.message
    assert without.key_source is None


def test_device_auth_never_carries_the_secret_itself() -> None:
    err = DeviceAuthFailed(
        transport=TransportKind.SSH,
        user="root",
        detail="passphrase wrong",
        key_source="~/.ssh/remarkable",
    )
    assert not hasattr(err, "password")
    assert not hasattr(err, "passphrase")


def test_malformed_device_metadata_degrades_to_an_anonymous_entry() -> None:
    named = MalformedDeviceMetadata(
        transport=TransportKind.SSH,
        detail="not json",
        document_uuid="1f0a",
    )
    anonymous = MalformedDeviceMetadata(transport=TransportKind.SSH, detail="not json")
    assert "document 1f0a" in named.message
    assert "an entry" in anonymous.message
    assert anonymous.document_uuid is None


def test_an_interrupted_transfer_reports_what_it_moved_and_what_it_expected() -> None:
    known = DeviceTransferInterrupted(
        transport=TransportKind.SSH,
        subject="bundle",
        bytes_transferred=1024,
        bytes_expected=8192,
    )
    unknown = DeviceTransferInterrupted(
        transport=TransportKind.SSH,
        subject="bundle",
        bytes_transferred=0,
    )
    assert "1024 bytes of 8192" in known.message
    assert "stopped after 0 bytes" in unknown.message
    assert "bytes of" not in unknown.message
    assert unknown.bytes_expected is None


def test_a_throttle_carries_the_providers_own_delay_when_it_gave_one() -> None:
    told = ModelThrottled(model_id="opus", retry_after_s=2.5)
    silent = ModelThrottled(model_id="opus")
    assert "retry after 2.5s" in told.message
    assert told.retry_after_s == 2.5
    assert "retry" not in silent.message
    assert silent.retry_after_s is None


def test_an_unsupported_operation_with_no_alternative_says_so() -> None:
    nowhere = DeviceOperationUnsupported(
        transport=TransportKind.USB_WEB_API,
        operation="raw bundle download",
        supported_by=(),
    )
    somewhere = DeviceOperationUnsupported(
        transport=TransportKind.USB_WEB_API,
        operation="raw bundle download",
        supported_by=(TransportKind.SSH,),
    )
    assert "no transport" in nowhere.message
    assert nowhere.remediation == "retry with no transport"
    assert "ssh" in somewhere.message
    assert somewhere.supported_by == (TransportKind.SSH,)


# ─────────────────────────── what individual errors have to say ───────────────────────────


def test_the_unconfigured_directory_names_every_source_consulted() -> None:
    consulted = ("--xochitl", "RMSPEC_XOCHITL", "~/.remarkable-spec/config.toml")
    err = XochitlDirNotConfigured(consulted=consulted)
    assert all(source in err.message for source in consulted)
    assert err.consulted == consulted


def test_the_unconfigured_directory_survives_having_consulted_nothing() -> None:
    err = XochitlDirNotConfigured(consulted=())
    assert err.consulted == ()
    assert err.message.endswith("consulted ")


def test_an_invalid_setting_quotes_the_value_and_names_the_requirement() -> None:
    err = InvalidSettingError(setting="render_dpi", value="0", requirement="a positive integer")
    assert "'0'" in err.message
    assert "a positive integer" in err.message
    assert err.remediation == "set render_dpi to a positive integer"


@given(query=TEXT, store=TEXT)
def test_a_missing_document_quotes_the_query_it_was_given(query: str, store: str) -> None:
    err = DocumentNotFound(query=query, store=store)
    assert store in err.message
    assert repr(query) in err.message
    assert (err.query, err.store) == (query, store)


@given(
    candidates=st.lists(st.builds(DocumentCandidate, uuid=TEXT, name=TEXT), min_size=2, max_size=6)
)
def test_an_ambiguous_query_counts_the_candidates_it_carries(
    candidates: list[DocumentCandidate],
) -> None:
    err = AmbiguousDocument(query="sprint", candidates=tuple(candidates))
    assert err.message.startswith(f"{len(candidates)} documents match")
    assert err.candidates == tuple(candidates)
    assert err.remediation == "repeat the query with a full document uuid"


def test_an_unsupported_page_format_names_the_observed_and_the_supported() -> None:
    err = UnsupportedPageFormat(
        page_uuid="9c1e",
        observed_version="3.0",
        supported_versions=("6.0", "6.1"),
    )
    assert "3.0" in err.message
    assert "6.0, 6.1" in err.message
    assert err.supported_versions == ("6.0", "6.1")


def test_an_unsupported_pen_is_carried_as_a_display_string_no_enum_covers() -> None:
    err = UnsupportedPenType(pen="pen-99", page_ref="9c1e")
    assert err.pen == "pen-99"
    assert "no physics model" in err.message


def test_a_pdf_page_out_of_range_states_both_bounds() -> None:
    err = PdfPageOutOfRange(source="paper.pdf", page_index=9, page_count=4)
    assert "4 pages" in err.message
    assert "index 9" in err.message


def test_an_artifact_write_failure_names_its_closed_reason() -> None:
    for reason in ArtifactWriteReason:
        err = ArtifactWriteFailed(name="page-01.png", reason=reason, detail="errno")
        assert reason.value in err.message
        assert err.reason is reason


def test_a_schema_mismatch_carries_both_versions_through_its_parent() -> None:
    err = StoreSchemaMismatchError(store="sync.db", found=3, expected=5)
    assert (err.found, err.expected) == (3, 5)
    assert err.store == "sync.db"
    assert "schema version 3 on disk, 5 expected" in err.detail
    assert "3" in err.message
    assert "5" in err.message


def test_an_unreadable_row_names_the_store_the_table_and_the_key() -> None:
    err = StoredRecordUnreadableError(
        store="sync.db",
        table="ocr_cache",
        key="9c1e",
        detail="a required column is null",
    )
    assert err.message.startswith("sync.db.ocr_cache row 9c1e")


def test_a_recognizer_failure_states_whether_a_retry_could_help() -> None:
    retryable = RecognitionFailed(provider_id="textract", detail="throttled", retryable=True)
    permanent = RecognitionFailed(provider_id="vision", detail="refused handler", retryable=False)
    assert retryable.retryable is True
    assert permanent.retryable is False
    assert "textract" in retryable.message


def test_every_recognizer_failing_names_all_of_them_in_a_stable_order() -> None:
    one_order = AllRecognizersFailed(failures={"vision": "refused", "textract": "throttled"})
    other_order = AllRecognizersFailed(failures={"textract": "throttled", "vision": "refused"})
    assert one_order.message == other_order.message
    assert "textract: throttled; vision: refused" in one_order.message


def test_every_recognizer_failing_copies_the_mapping_it_was_handed() -> None:
    failures = {"vision": "refused"}
    err = AllRecognizersFailed(failures=failures)
    failures["textract"] = "added afterwards"
    assert dict(err.failures) == {"vision": "refused"}


def test_no_text_recognized_lists_the_engines_that_all_came_back_empty() -> None:
    err = NoTextRecognized(page_ref="9c1e", providers=("vision", "textract"))
    assert "vision, textract" in err.message
    assert err.providers == ("vision", "textract")


def test_model_access_denied_names_the_model_and_takes_its_remediation_as_prose() -> None:
    # No `region` argument. A region is `boto3`'s `region_name` and nothing else has one, so
    # requiring it forced every non-AWS adapter to fabricate "n/a" and emit "not available to
    # this caller in n/a". The deployment detail now travels as prose the adapter authors.
    err = ModelAccessDenied(
        model_id="opus",
        remediation="enable opus in us-west-2 in the Bedrock console",
    )
    assert err.model_id == "opus"
    assert err.remediation == "enable opus in us-west-2 in the Bedrock console"
    assert "us-west-2" not in err.message


def test_nothing_can_read_a_region_back_off_an_access_denial() -> None:
    # An app that reads `exc.region` has imported AWS by another name, so the attribute does not
    # exist to be read -- and the constructor has no parameter that could put one there.
    err = ModelAccessDenied(model_id="opus", remediation="ask the account admin")
    parameters = inspect.signature(ModelAccessDenied.__init__).parameters

    assert not hasattr(err, "region")
    assert set(parameters) == {"self", "model_id", "remediation"}


def test_an_unreachable_model_is_not_a_throttle_and_says_whether_to_retry() -> None:
    # The distinction the fifth error exists for: an outage reported as a throttle sends the
    # caller into a retry loop that cannot succeed, and one reported as an entitlement failure
    # stops a run that a retry would have completed.
    transient = ModelUnavailable(
        endpoint="localhost:11434",
        detail="connect refused",
        retryable=True,
    )
    permanent = ModelUnavailable(
        endpoint="localhost:11434",
        detail="no such host",
        retryable=False,
    )
    assert transient.retryable is True
    assert permanent.retryable is False
    assert (transient.endpoint, transient.detail) == ("localhost:11434", "connect refused")
    assert not isinstance(transient, ModelThrottled)
    assert not isinstance(transient, ModelAccessDenied)
    assert transient.remediation == "check the endpoint, the region and the network"


def test_an_unreachable_model_takes_the_shape_the_device_slice_already_established() -> None:
    # An unreachable endpoint is a house-wide failure mode, not a model-slice novelty, so this
    # error carries the same three facts `DeviceUnreachable` does -- minus the transport, which
    # is a device concept -- rather than inventing a second vocabulary for one situation.
    model = ModelUnavailable(endpoint="10.11.99.1", detail="connect refused", retryable=True)
    device = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connect refused",
    )
    assert (model.endpoint, model.detail) == (device.endpoint, device.detail)
    assert not hasattr(model, "transport")


def test_a_rejected_request_and_a_malformed_answer_read_differently() -> None:
    rejected = ModelRejectedRequest(model_id="opus", detail="payload too big")
    malformed = ModelResponseMalformed(model_id="opus", detail="no text block")
    assert "rejected the request" in rejected.message
    assert "not a completion" in malformed.message
    assert rejected.code != malformed.code


def test_an_audit_write_failure_says_the_operation_may_have_landed() -> None:
    err = AuditWriteFailedError(detail="the history table is locked")
    assert err.message == "history entry not recorded: the history table is locked"
    assert isinstance(err, PersistenceError)


def test_a_usage_error_points_at_the_command_line_just_typed() -> None:
    err = UsageError(subject="--strict with --force", requirement="at most one of them")
    assert err.remediation == "retry with at most one of them"
    assert exit_code(err) == 2


# ─────────────────────────────── the transport enum ───────────────────────────────


def test_every_device_failure_carries_the_transport_that_failed() -> None:
    device_samples = [err for err in SAMPLES if isinstance(err, DeviceError)]
    assert len(device_samples) == 9
    assert all(isinstance(err.transport, TransportKind) for err in device_samples)


def test_the_transport_appears_in_the_messages_that_advise_on_it() -> None:
    err = DeviceUnreachable(
        transport=TransportKind.USB_WEB_API,
        endpoint="10.11.99.1",
        detail="connect refused",
    )
    assert "usb_web_api" in err.message
    assert err.remediation == "check the cable and the host"


def test_the_transport_set_is_closed_at_three_members() -> None:
    assert {kind.value for kind in TransportKind} == {"usb_web_api", "ssh", "local_mirror"}


def test_an_unknown_transport_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="bluetooth"):
        TransportKind("bluetooth")


# ─────────────────────────────── the closed enums ───────────────────────────────


@pytest.mark.parametrize("enum_cls", CLOSED_ENUMS, ids=lambda cls: cls.__name__)
def test_closed_enums_are_string_enums_whose_value_is_their_lowercased_name(
    enum_cls: type[StrEnum],
) -> None:
    for member in enum_cls:
        assert member.value == member.name.lower()
        assert f"{member}" == member.value
        assert enum_cls(member.value) is member


@pytest.mark.parametrize("enum_cls", CLOSED_ENUMS, ids=lambda cls: cls.__name__)
def test_closed_enums_have_no_aliases(enum_cls: type[StrEnum]) -> None:
    members = list(enum_cls)
    assert len({member.value for member in members}) == len(members)


def test_the_degradation_vocabulary_is_closed_at_the_reviewed_eight() -> None:
    """The count is in the name so that adding a member cannot be a quiet edit.

    It went from seven to eight on 2026-08-30, and this test failing is what made that a
    decision rather than a diff: two use cases had independently reached for
    ``CATALOG_ENTRY_SKIPPED`` for an unavailable device index and each said in prose that it
    did not fit.
    """
    assert {kind.value for kind in DegradationKind} == {
        "catalog_entry_skipped",
        "page_not_annotated",
        "ambiguous_auto_resolved",
        "pdf_page_index_fallback",
        "pdf_page_count_estimated",
        "cache_miss_key_changed",
        "audit_not_recorded",
        "device_index_unavailable",
    }


def test_an_unavailable_device_index_is_its_own_kind_and_not_a_skipped_entry() -> None:
    """The two are about different things and a reader acts on them differently.

    A skipped catalog entry means "look again for that document"; an unavailable index means
    "nothing is missing from your answer except a cross-check you were not paying for". The
    assertion is that they are distinct members rather than one stretched over both, which
    is the state this replaced.
    """
    assert DegradationKind.DEVICE_INDEX_UNAVAILABLE is not DegradationKind.CATALOG_ENTRY_SKIPPED
    assert DegradationKind.DEVICE_INDEX_UNAVAILABLE.value == "device_index_unavailable"


def test_the_write_reason_vocabulary_is_closed_at_four() -> None:
    assert {reason.value for reason in ArtifactWriteReason} == {
        "already_present",
        "not_writable",
        "out_of_space",
        "interrupted",
    }


def test_a_stale_cache_key_is_a_named_miss_and_not_an_error_class() -> None:
    assert DegradationKind.CACHE_MISS_KEY_CHANGED in set(DegradationKind)
    assert not hasattr(errors_module, "CacheKeyMismatchError")
    assert "CacheKeyMismatchError" not in errors_module.__all__


# ─────────────────────────────── the two carried models ───────────────────────────────


def test_a_degradation_is_data_and_not_an_exception() -> None:
    degradation = Degradation(
        kind=DegradationKind.PAGE_NOT_ANNOTATED,
        subject="9c1e",
        detail="no annotation artifact",
    )
    assert not isinstance(degradation, BaseException)
    assert not issubclass(Degradation, BaseException)


def test_a_degradation_that_skipped_substituted_nothing() -> None:
    degradation = Degradation(
        kind=DegradationKind.CATALOG_ENTRY_SKIPPED,
        subject="1f0a",
        detail="metadata is not json",
    )
    assert degradation.substituted is None


def test_a_degradation_that_substituted_carries_the_value_it_used() -> None:
    degradation = Degradation(
        kind=DegradationKind.PDF_PAGE_INDEX_FALLBACK,
        subject="9c1e",
        detail="no redirection entry",
        substituted="3",
    )
    assert degradation.substituted == "3"


def test_a_degradation_accepts_its_kind_as_the_wire_value() -> None:
    degradation = Degradation(
        kind=cast("DegradationKind", "audit_not_recorded"),
        subject="sync.db",
        detail="locked",
    )
    assert degradation.kind is DegradationKind.AUDIT_NOT_RECORDED


def test_a_degradation_rejects_a_kind_outside_the_closed_set() -> None:
    with pytest.raises(ValidationError):
        Degradation(
            kind=cast("DegradationKind", "quietly_guessed"),
            subject="9c1e",
            detail="a fallback nobody reviewed",
        )


def test_a_degradation_is_frozen_and_hashable() -> None:
    degradation = Degradation(
        kind=DegradationKind.AUDIT_NOT_RECORDED,
        subject="sync.db",
        detail="locked",
    )
    assert len({degradation, degradation.model_copy()}) == 1
    with pytest.raises(ValidationError):
        degradation.subject = "another"  # ty: ignore[invalid-assignment]


def test_a_degradation_forbids_fields_nobody_declared() -> None:
    with pytest.raises(ValidationError):
        Degradation(
            kind=DegradationKind.AUDIT_NOT_RECORDED,
            subject="sync.db",
            detail="locked",
            severity="warning",  # ty: ignore[unknown-argument]
        )


def test_a_degradation_carries_no_filesystem_path_type() -> None:
    fields = Degradation.model_fields
    assert {name: field.annotation for name, field in fields.items()} == {
        "kind": DegradationKind,
        "subject": str,
        "detail": str,
        "substituted": str | None,
    }


def test_a_candidate_needs_a_uuid_the_user_can_retype() -> None:
    with pytest.raises(ValidationError):
        DocumentCandidate(uuid="", name="Sprint notes")


def test_a_candidate_may_have_the_empty_name_the_tablet_shows() -> None:
    candidate = DocumentCandidate(uuid="1f0a", name="")
    assert candidate.name == ""


def test_a_candidate_is_frozen_and_forbids_extra_fields() -> None:
    candidate = DocumentCandidate(uuid="1f0a", name="Sprint notes")
    with pytest.raises(ValidationError):
        candidate.uuid = "2b71"  # ty: ignore[invalid-assignment]
    with pytest.raises(ValidationError):
        DocumentCandidate(
            uuid="1f0a",
            name="Sprint notes",
            page_count=4,  # ty: ignore[unknown-argument]
        )


@given(uuid=TEXT, name=st.text(max_size=40))
def test_a_candidate_round_trips_through_its_own_dump(uuid: str, name: str) -> None:
    candidate = DocumentCandidate(uuid=uuid, name=name)
    assert DocumentCandidate.model_validate(candidate.model_dump()) == candidate


@given(
    kind=st.sampled_from(DegradationKind),
    subject=st.text(max_size=40),
    detail=st.text(max_size=40),
    substituted=st.none() | st.text(max_size=40),
)
def test_a_degradation_round_trips_through_its_own_dump(
    kind: DegradationKind,
    subject: str,
    detail: str,
    substituted: str | None,
) -> None:
    degradation = Degradation(
        kind=kind,
        subject=subject,
        detail=detail,
        substituted=substituted,
    )
    assert Degradation.model_validate(degradation.model_dump()) == degradation
