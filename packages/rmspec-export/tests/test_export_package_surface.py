"""Structural invariants this package must keep, asserted rather than written down.

Three of them cost nothing and each pins a defect that was live in the legacy tree: a
third-party import in the wrong module, an availability ``ImportError`` reachable from a port
method, and a backend exception escaping to a caller.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

import rmspec.export as export_package
from rmspec.domain.errors import ExportError, MissingDependencyError, RmspecError

SOURCE_ROOT = pathlib.Path(export_package.__file__).parent

#: Third-party top-level modules this package owns, and the one private module allowed to
#: import each. Enforced here as well as by the workspace architecture suite, because that suite
#: checks only *which package* may import them and not that each stays behind one shim.
DYLD_VARIABLE = "DYLD_FALLBACK_LIBRARY_PATH"

OWNERS = {
    "cairosvg": "_cairo.py",
    "cairocffi": "_cairo.py",
    "pymupdf": "_pymupdf.py",
    "fitz": "_pymupdf.py",
    "PIL": "_pillow.py",
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _source_files() -> list[pathlib.Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_source_files_were_discovered() -> None:
    assert len(_source_files()) > 5, "an empty sweep would make the assertions below vacuous"


@pytest.mark.parametrize(("module", "owner"), sorted(OWNERS.items()))
def test_each_backend_is_imported_by_exactly_one_private_shim(module: str, owner: str) -> None:
    importers = sorted(path.name for path in _source_files() if module in _imports(path))
    assert importers in ([], [owner]), (
        f"{module} must be imported only by {owner}, but is imported by {importers}"
    )


def test_the_public_surface_is_the_four_adapters_plus_wiring() -> None:
    assert export_package.__all__ == [
        "CairoSvgPdfComposer",
        "CairoSvgRasterizer",
        "FilesystemArtifactSink",
        "PdfSourceRegistry",
        "PyMuPdfPageReader",
        "require_backends",
    ]


def test_every_public_name_resolves() -> None:
    for name in export_package.__all__:
        assert getattr(export_package, name) is not None


def test_no_module_raises_import_error_anywhere() -> None:
    # The legacy tree raised ImportError from five function-local guards, including a double
    # raise of the same message. Availability is a composition concern now: the only error a
    # backend problem may produce is MissingDependencyError, off the root rather than under
    # ExportError, so no use case's `except ExportError` can swallow a wiring bug.
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                function = node.exc.func
                if isinstance(function, ast.Name) and function.id == "ImportError":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_missing_dependency_is_not_an_export_error() -> None:
    assert issubclass(MissingDependencyError, RmspecError)
    assert not issubclass(MissingDependencyError, ExportError)


def test_the_package_imports_in_a_fresh_interpreter_with_no_library_path() -> None:
    # A subprocess, because the point cannot be made in-process: this interpreter has already
    # imported cairosvg. tests/architecture/test_packages_importable.py imports this package with
    # no DYLD_FALLBACK_LIBRARY_PATH in its environment, so if the seeding in _dyld regressed that
    # suite -- not this one -- would go red with an OSError that reads like a missing wheel.
    environment = {key: value for key, value in os.environ.items() if key != DYLD_VARIABLE}
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import rmspec.export"],
        check=False,
        capture_output=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_an_unguarded_pymupdf_import_would_still_take_down_the_session() -> None:
    # The guard in _pymupdf is load-bearing only while this remains true. When it stops being
    # true this test fails, which is the signal to re-measure on every supported platform before
    # removing the guard -- not a reason to delete the assertion.
    unguarded = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import pymupdf"],
        check=False,
        capture_output=True,
    )
    guarded = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import rmspec.export._pymupdf"],
        check=False,
        capture_output=True,
    )
    assert unguarded.returncode != 0, (
        "pymupdf now imports cleanly under -W error; the catch_warnings guard in _pymupdf may be "
        "removable, but re-measure on every supported platform before deleting it"
    )
    assert guarded.returncode == 0, guarded.stderr.decode()
