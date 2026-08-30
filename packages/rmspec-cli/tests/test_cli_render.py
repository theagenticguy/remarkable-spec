"""What ``rmspec render`` must do: read both flags, cap the work, and commit bytes once.

Nothing here reaches a device, a native drawing library or a model. The render pipeline
arrives as a stub bound over :class:`~rmspec.app.RenderPages` with ``override=True``, which
is what lets a test assert the *request* the command built -- the only place ``--dpi`` and
``--thickness`` can be proved to have been read rather than accepted and dropped.

``providers=`` is :func:`~rmspec.cli._invoke.run`'s test-only hook and a command signature
deliberately does not carry it, so ``_bind`` puts the overrides on the ``run`` the module
under test calls. That keeps the hook out of ``--help`` and out of ``rmspec manifest``.
"""

from __future__ import annotations

import errno
import functools
import json as json_module
import os
from typing import TYPE_CHECKING, Any, cast

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import (
    PageSelection,
    RenderedPageArtifact,
    RenderPages,
    RenderPagesRequest,
    RenderPagesResult,
)
from rmspec.cli import _render
from rmspec.cli._invoke import PAGE_SPEC_GRAMMAR, run
from rmspec.cli._render import _FLAG_TEXT, _RENDER_COLUMNS, _reason, render
from rmspec.cli._settings import HOMEBREW_LIBRARY_DIR, NATIVE_LIBRARY_PATH_VAR
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import ArtifactWriteReason
from rmspec.domain.ports.device import DeviceCatalog, DeviceDocument, DeviceFileType
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.export import (
    ImageMedia,
    PdfComposer,
    PdfDocument,
    PdfPageRef,
    PhysicalSize,
    RasterImage,
    SvgPageSet,
)
from rmspec.domain.ports.render import PhysicalSize as RenderPhysicalSize
from rmspec.domain.ports.render import RenderedPage

if TYPE_CHECKING:
    from pathlib import Path

_OVERRIDDEN_PORTS = (DependencyProbe,)
"""The port :class:`_ProbeProvider` binds a double over, listed to give it a runtime use.

dishka reads a provider's return annotation at container build to learn what it provides,
so moving the name into an ``if TYPE_CHECKING:`` block makes the container raise instead of
resolving -- and this repo allows neither a ``noqa`` nor a ``type: ignore``.
``_container.BOUND_PORTS`` is the same discipline.
"""

_ONE = "a" * 8
_TWO = "b" * 8

_DOCUMENTS = (
    DeviceDocument(
        uuid=_ONE,
        name="Notes one",
        file_type=DeviceFileType.NOTEBOOK,
        page_count=3,
    ),
    DeviceDocument(
        uuid=_TWO,
        name="Notes two",
        file_type=DeviceFileType.NOTEBOOK,
        page_count=1,
    ),
)

_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
"""The smallest byte string :class:`~rmspec.domain.ports.export.PdfDocument` accepts."""


def _png(width: int, height: int) -> bytes:
    """Build the smallest byte string a raster of this size validates against."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
    )


def _artifact(index: int, /, *, raster: bool = True) -> RenderedPageArtifact:
    """Build one rendered page, optionally without the pixels a PNG export needs."""
    ref = f"page-ref-{index}"
    pixels = (
        RasterImage(
            page_ref=ref,
            media=ImageMedia.PNG,
            data=_png(2, 3),
            width=2,
            height=3,
            render_dpi=226,
        )
        if raster
        else None
    )
    return RenderedPageArtifact(
        page_ref=ref,
        page_index=index,
        page_hash="0" * 64,
        rendered=RenderedPage(
            page_ref=ref,
            svg=f'<svg id="{index}"></svg>',
            size=RenderPhysicalSize(width_mm=210.0, height_mm=297.0),
            stroke_count=index + 1,
            text_block_count=0,
        ),
        raster=pixels,
    )


def _result(*artifacts: RenderedPageArtifact) -> RenderPagesResult:
    """Build a render result over the given pages."""
    return RenderPagesResult(
        document_uuid=_ONE,
        pages=artifacts,
        render_digest="d" * 64,
        degradations=(),
    )


class _StubRender:
    """A :class:`~rmspec.app.RenderPages` that records requests and renders nothing."""

    def __init__(self, result: RenderPagesResult) -> None:
        self.result = result
        self.requests: list[RenderPagesRequest] = []

    def render(self, request: RenderPagesRequest, /) -> RenderPagesResult:
        """Record the request and answer with the fixed result."""
        self.requests.append(request)
        return self.result


class _StubComposer:
    """A :class:`~rmspec.domain.ports.export.PdfComposer` that composes a constant."""

    def __init__(self) -> None:
        self.page_sets: list[SvgPageSet] = []

    def compose(self, pages: SvgPageSet) -> PdfDocument:
        """Record the page set and answer with one valid document."""
        self.page_sets.append(pages)
        return PdfDocument(
            data=_PDF_BYTES,
            pages=tuple(
                PdfPageRef(page_ref=page.page_ref, size=page.size) for page in pages.pages
            ),
        )


class _ProbeDouble:
    """A :class:`~rmspec.domain.ports.errors.DependencyProbe` answering from one set."""

    def __init__(self, *, absent: frozenset[str] = frozenset()) -> None:
        self._absent = absent
        self.asked: list[str] = []

    def is_installed(self, module_name: str, /) -> bool:
        """Report whether the module was left out of this double's world."""
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, module_name: str, /) -> str | None:
        """Report that every installed module loads, which keeps the probe honest."""
        assert module_name not in self._absent
        return None


class _RenderProvider(Provider):
    """Bind the stub pipeline over the real, device-backed use case."""

    scope = Scope.REQUEST

    def __init__(self, stub: _StubRender) -> None:
        super().__init__()
        self._stub = stub

    @provide(override=True)
    def pages(self) -> RenderPages:
        """Answer with the stub, cast because it stands in rather than subclasses."""
        return cast("RenderPages", self._stub)


class _CatalogProvider(Provider):
    """Bind the shipped in-memory catalog, so nothing here opens a transport."""

    scope = Scope.REQUEST

    @provide(override=True)
    def catalog(self) -> DeviceCatalog:
        """Answer with two documents whose names share a substring."""
        return InMemoryDeviceCatalog(documents=_DOCUMENTS)


class _ComposerProvider(Provider):
    """Bind a PDF composer that never touches a native library."""

    scope = Scope.APP

    def __init__(self, stub: _StubComposer) -> None:
        super().__init__()
        self._stub = stub

    @provide(override=True)
    def composer(self) -> PdfComposer:
        """Answer with the stub."""
        return self._stub


class _ProbeProvider(Provider):
    """Bind a probe that answers from a table instead of from the interpreter."""

    scope = Scope.APP

    def __init__(self, probe: _ProbeDouble) -> None:
        super().__init__()
        self._probe = probe

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        """Answer with the double."""
        return self._probe


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test here independent of the developer's own exported ``RMSPEC_*``."""
    for name in list(os.environ):
        if name.startswith("RMSPEC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv(NATIVE_LIBRARY_PATH_VAR, HOMEBREW_LIBRARY_DIR)


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    stub: _StubRender,
    /,
    *,
    probe: _ProbeDouble | None = None,
    composer: _StubComposer | None = None,
) -> None:
    """Put every override on the ``run`` the command module calls."""
    providers = [
        _RenderProvider(stub),
        _CatalogProvider(),
        _ComposerProvider(_StubComposer() if composer is None else composer),
        _ProbeProvider(_ProbeDouble() if probe is None else probe),
    ]
    monkeypatch.setattr(_render, "run", functools.partial(run, providers=providers))


def _records(text: str, /) -> list[list[str]]:
    """Split ``DENSE`` output into its records."""
    return [line.split("\t") for line in text.splitlines()]


def _envelope(text: str, /) -> dict[str, Any]:
    """Parse the one JSON document a ``--json`` run writes to stdout."""
    parsed = json_module.loads(text)
    assert isinstance(parsed, dict)
    return parsed


def _failure(text: str, /) -> dict[str, Any]:
    """Take the ``error`` object out of a failure envelope."""
    error = _envelope(text)["error"]
    assert isinstance(error, dict)
    return error


def _sole(stub: _StubRender, /) -> RenderPagesRequest:
    """Take the one request the command built."""
    assert len(stub.requests) == 1
    return stub.requests[0]


def _names(destination: Path, /) -> list[str]:
    """List what the export left in a directory, sorted."""
    return sorted(path.name for path in destination.iterdir())


def test_svg_export_writes_one_file_per_page_and_reports_every_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0), _artifact(1)))
    _bind(monkeypatch, stub)
    out = tmp_path / "export"

    status = render("Notes one", out, dense=True)

    assert status == 0
    assert _names(out) == ["page-0000.svg", "page-0001.svg"]
    assert (out / "page-0000.svg").read_bytes() == b'<svg id="0"></svg>'
    records = _records(capsys.readouterr().out)
    assert records[0] == list(_RENDER_COLUMNS)
    assert [record[0] for record in records[1:]] == ["page-0000", "page-0001"]
    assert {record[1] for record in records[1:]} == {"svg"}
    assert {record[4] for record in records[1:]} == {_FLAG_TEXT[True]}
    assert records[1][2] == (out / "page-0000.svg").as_uri()
    assert records[1][3] == str(len(b'<svg id="0"></svg>'))


def test_json_carries_the_render_result_and_the_receipts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out", json=True)

    assert status == 0
    document = _envelope(capsys.readouterr().out)
    assert document["type"] == "render"
    assert document["degradations"] == []
    data = document["data"]
    assert data["render_digest"] == "d" * 64
    assert data["document_uuid"] == _ONE
    assert data["pages"][0]["page_index"] == 0
    artifact = data["artifacts"][0]
    assert artifact["name"] == {"value": "page-0000"}
    assert artifact["media"] == "svg"
    assert artifact["committed"] is True


def test_human_mode_puts_its_table_on_stderr_and_writes_nothing_to_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out")

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == ""
    assert "page-0000" in captured.err


def test_png_export_writes_the_pixels_and_both_flags_reach_the_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)
    out = tmp_path / "out"

    status = render("Notes one", out, fmt="png", dpi=400, thickness=2.5)

    assert status == 0
    assert (out / "page-0000.png").read_bytes() == _png(2, 3)
    request = _sole(stub)
    assert request.raster_dpi == 400
    assert request.style.thickness_scale == 2.5


def test_pdf_export_composes_one_document_and_both_flags_reach_the_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0), _artifact(1)))
    composer = _StubComposer()
    _bind(monkeypatch, stub, composer=composer)
    out = tmp_path / "out"

    status = render("Notes one", out, fmt="pdf", dpi=400, thickness=2.5)

    assert status == 0
    assert _names(out) == [f"{_ONE}.pdf"]
    assert (out / f"{_ONE}.pdf").read_bytes() == _PDF_BYTES
    composed = composer.page_sets[0].pages
    assert [page.page_ref for page in composed] == ["page-ref-0", "page-ref-1"]
    assert composed[0].size == PhysicalSize(width_mm=210.0, height_mm=297.0)
    request = _sole(stub)
    assert request.raster_dpi == 400
    assert request.style.thickness_scale == 2.5


def test_the_default_density_is_the_render_setting_and_never_the_ocr_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_RENDER_DPI", "111")
    monkeypatch.setenv("RMSPEC_OCR_DPI", "222")
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    assert render("Notes one", tmp_path / "out", fmt="png") == 0
    assert _sole(stub).raster_dpi == 111


def test_the_default_thickness_is_the_setting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_THICKNESS", "3.25")
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    assert render("Notes one", tmp_path / "out") == 0
    assert _sole(stub).style.thickness_scale == 3.25


def test_an_svg_export_rasterizes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    assert render("Notes one", tmp_path / "out") == 0
    assert _sole(stub).raster_dpi is None


def test_a_resolution_is_refused_for_a_format_that_has_none(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out", dpi=300, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
    assert stub.requests == []


@pytest.mark.parametrize("dpi", [0, -1])
def test_a_non_positive_resolution_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    dpi: int,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out", fmt="png", dpi=dpi, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_a_non_positive_thickness_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out", thickness=0.0, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_an_artifact_already_there_is_refused_until_overwrite_is_passed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)
    out = tmp_path / "out"
    out.mkdir()
    (out / "page-0000.svg").write_bytes(b"older")

    status = render("Notes one", out, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "ArtifactWriteFailed"
    assert error["exit_code"] == status
    assert (out / "page-0000.svg").read_bytes() == b"older"

    assert render("Notes one", out, overwrite=True) == 0
    assert (out / "page-0000.svg").read_bytes() == b'<svg id="0"></svg>'


def test_a_dry_run_predicts_every_artifact_and_touches_the_filesystem_not_at_all(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)
    out = tmp_path / "out"

    status = render("Notes one", out, dry_run=True, dense=True)

    assert status == 0
    assert not out.exists()
    records = _records(capsys.readouterr().out)
    assert records[1][0] == "page-0000"
    assert records[1][2] == (out / "page-0000.svg").as_uri()
    assert records[1][4] == _FLAG_TEXT[False]


def test_a_document_with_no_pages_writes_nothing_and_creates_no_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result())
    _bind(monkeypatch, stub)
    out = tmp_path / "out"

    status = render("Notes one", out, dense=True)

    assert status == 0
    assert not out.exists()
    assert _records(capsys.readouterr().out) == [list(_RENDER_COLUMNS)]


def test_a_page_that_came_back_without_pixels_is_refused_rather_than_written_empty(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0, raster=False)))
    _bind(monkeypatch, stub)
    out = tmp_path / "out"

    status = render("Notes one", out, fmt="png", json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "RasterizationFailed"
    assert error["exit_code"] == status
    assert not out.exists()


def test_an_output_directory_that_cannot_be_created_is_reported_as_not_writable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", blocker / "out", json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "ArtifactWriteFailed"
    assert error["exit_code"] == status
    assert "could not be created" in error["message"]
    assert ArtifactWriteReason.NOT_WRITABLE.value in error["message"]


def test_a_failed_commit_reports_a_reason_and_leaves_no_scratch_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)
    out = tmp_path / "out"
    (out / "page-0000.svg").mkdir(parents=True)

    status = render("Notes one", out, overwrite=True, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "ArtifactWriteFailed"
    assert error["exit_code"] == status
    assert _names(out) == ["page-0000.svg"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (OSError(errno.ENOSPC, "no space"), ArtifactWriteReason.OUT_OF_SPACE),
        (OSError(errno.EACCES, "denied"), ArtifactWriteReason.NOT_WRITABLE),
        (OSError(errno.EPIPE, "broken"), ArtifactWriteReason.INTERRUPTED),
        (OSError("no errno at all"), ArtifactWriteReason.INTERRUPTED),
    ],
)
def test_every_filesystem_failure_maps_to_a_reason_the_domain_names(
    error: OSError,
    expected: ArtifactWriteReason,
) -> None:
    assert _reason(error) is expected


def test_the_probe_runs_before_the_command_touches_a_use_case(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub, probe=_ProbeDouble(absent=frozenset({"rmscene"})))

    status = render("Notes one", tmp_path / "out", json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "MissingDependencyError"
    assert error["exit_code"] == status
    assert stub.requests == []
    assert not (tmp_path / "out").exists()


def test_only_a_raster_format_needs_the_raster_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub, probe=_ProbeDouble(absent=frozenset({"cairosvg"})))

    assert render("Notes one", tmp_path / "svg") == 0
    assert render("Notes one", tmp_path / "png", fmt="png") != 0


def test_the_work_cap_comes_from_the_setting_and_the_flag_overrides_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RMSPEC_MAX_PAGES", "7")
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    assert render("Notes one", tmp_path / "a") == 0
    assert stub.requests[0].max_pages == 7

    assert render("Notes one", tmp_path / "b", max_pages=2) == 0
    assert stub.requests[1].max_pages == 2


def test_the_pages_flag_is_zero_based(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(1)))
    _bind(monkeypatch, stub)

    assert render("Notes one", tmp_path / "out", pages="1") == 0
    assert _sole(stub).selection == PageSelection.of(1)


def test_the_help_text_repeats_the_shared_page_grammar() -> None:
    assert render.__doc__ is not None
    assert PAGE_SPEC_GRAMMAR in " ".join(render.__doc__.split())


def test_an_ambiguous_selector_is_accepted_and_the_substitution_is_hoisted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes", tmp_path / "out", json=True)

    assert status == 0
    degradations = _envelope(capsys.readouterr().out)["degradations"]
    assert [item["kind"] for item in degradations] == ["ambiguous_auto_resolved"]


def test_strict_refuses_an_ambiguous_selector_before_anything_is_rendered(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes", tmp_path / "out", strict=True, json=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "AmbiguousDocument"
    assert error["exit_code"] == status
    assert stub.requests == []


def test_both_output_flags_together_are_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubRender(_result(_artifact(0)))
    _bind(monkeypatch, stub)

    status = render("Notes one", tmp_path / "out", json=True, dense=True)

    error = _failure(capsys.readouterr().out)
    assert error["type"] == "UsageError"
    assert error["exit_code"] == status
