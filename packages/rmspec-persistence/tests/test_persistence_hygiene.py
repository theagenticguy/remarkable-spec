"""Containment rules this package must keep, asserted rather than reviewed.

Four of them. ``sqlite3`` is confined to one module inside the one package allowed
to import it at all, so a future adapter cannot quietly grow a second connect site
with a different pragma block -- and a pragma block is per-connection, so one that
forgets ``foreign_keys`` stops the cascade with no error. Nothing in the package or
its tests imports a cloud client or a network library or names the tablet: the
tablet is not attached and a billable call has no place in a unit test. No module
resolves ``Path.home()`` except the one function whose job is to say where the
database conventionally lives. And no branch in the package decides a miss by
testing a value for truthiness, because "the page was read and held nothing" is a
value -- which is what 62 of the corpus's 92 pages are.

Imports are read with ``ast`` rather than by substring, the way
``tests/architecture/`` does it: a docstring that *mentions* ``sqlite3`` is not an
import of it, and a check that cannot tell the difference is a check nobody can
write documentation around.
"""

from __future__ import annotations

import ast
import pathlib
from typing import TYPE_CHECKING, Final

import pytest

from rmspec.persistence import default_database_path

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT: Final = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT: Final = PACKAGE_ROOT / "src"
TESTS_ROOT: Final = PACKAGE_ROOT / "tests"
DOUBLES_ROOT: Final = SOURCE_ROOT / "rmspec" / "persistence" / "testing"

#: Modules that have no business being imported anywhere in this package.
#: ``socket`` stands in for every network client at the import level.
FORBIDDEN_IMPORTS: Final = frozenset(
    {"boto3", "botocore", "paramiko", "httpx", "requests", "socket", "urllib"},
)

#: The tablet's USB-Ethernet endpoint. No test may name it.
DEVICE_ADDRESS: Final = "10.11.99.1"

#: Names a miss must never be decided by. Only ``row is None`` may mean a miss,
#: and only a failed validation may mean unreadable.
TRUTHINESS_BANNED: Final = frozenset({"payload", "artifact", "row", "text", "mermaid"})


def _python_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yield every Python file under ``root``.

    Parameters
    ----------
    root
        Directory to walk.

    Yields
    ------
    pathlib.Path
        Each ``.py`` file.
    """
    yield from sorted(root.rglob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Return the top-level module names ``path`` imports, function-locals included.

    Parameters
    ----------
    path
        The file to parse.

    Returns
    -------
    set[str]
        Top-level module names.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _dotted_imports(path: pathlib.Path) -> set[str]:
    """Return the full dotted module names ``path`` imports.

    Parameters
    ----------
    path
        The file to parse.

    Returns
    -------
    set[str]
        Dotted module names, as written.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _truthiness_lines(path: pathlib.Path) -> list[int]:
    """Return the lines where a banned name is used as a condition.

    Parameters
    ----------
    path
        The file to parse.

    Returns
    -------
    list[int]
        Line numbers of ``if name:`` and ``if not name:`` over a banned name.
        ``if not text.strip()`` over a file's contents is not one of these: it is
        an attribute call, and a blank sidecar genuinely is nothing to import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            test = test.operand
        if isinstance(test, ast.Name) and test.id in TRUTHINESS_BANNED:
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("module", sorted(FORBIDDEN_IMPORTS))
def test_nothing_imports_a_cloud_client_or_a_network_library(module: str) -> None:
    hits = [
        str(path.relative_to(PACKAGE_ROOT))
        for root in (SOURCE_ROOT, TESTS_ROOT)
        for path in _python_files(root)
        if module in _imported_modules(path)
    ]
    assert hits == []


def test_no_file_names_the_tablet() -> None:
    hits = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for root in (SOURCE_ROOT, TESTS_ROOT)
        for path in _python_files(root)
        if path.name != pathlib.Path(__file__).name
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if DEVICE_ADDRESS in line
    ]
    assert hits == []


def test_only_one_source_module_imports_the_driver() -> None:
    importers = sorted(
        path.name for path in _python_files(SOURCE_ROOT) if "sqlite3" in _imported_modules(path)
    )
    assert importers == ["_sqlite.py"]


def test_the_doubles_import_neither_the_driver_nor_an_adapter() -> None:
    # Every later application-layer test binds these, so they must stay free of
    # the driver, the file and the adapter modules.
    adapters = {
        "rmspec.persistence._sqlite",
        "rmspec.persistence.sync_store",
        "rmspec.persistence.caches",
        "rmspec.persistence.audit_log",
        "rmspec.persistence.maintenance",
    }
    for path in _python_files(DOUBLES_ROOT):
        imported = _dotted_imports(path)
        assert "sqlite3" not in imported
        assert not imported & adapters


def test_no_module_resolves_the_home_directory_except_the_path_helper() -> None:
    users = sorted(
        path.name
        for path in _python_files(SOURCE_ROOT)
        if "Path.home()" in path.read_text(encoding="utf-8")
    )
    assert users == ["paths.py"]


def test_no_branch_decides_a_miss_by_truthiness() -> None:
    hits = [
        f"{path.relative_to(PACKAGE_ROOT)}:{lineno}"
        for path in _python_files(SOURCE_ROOT)
        for lineno in _truthiness_lines(path)
    ]
    assert hits == []


def test_the_truthiness_check_can_actually_fail(tmp_path: pathlib.Path) -> None:
    # Guard the guard. A check that cannot fail is worse than no check, because it
    # reads as protection.
    probe = tmp_path / "probe.py"
    probe.write_text("def f(payload):\n    if not payload:\n        return 1\n", encoding="utf-8")
    assert _truthiness_lines(probe) == [2]


def test_the_default_database_path_is_resolved_per_call_not_at_import(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A module-level constant would have baked the developer's own home directory
    # into every importing process and made this unpatchable, which is what the
    # legacy DEFAULT_DB_PATH did.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_database_path() == tmp_path / ".remarkable-spec" / "sync.db"


def test_the_default_database_path_creates_nothing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = default_database_path()
    assert not path.exists()
    assert not path.parent.exists()


def test_only_this_module_asks_for_the_default_database_path() -> None:
    # Everything else in the suite opens a database under tmp_path, so no test can
    # touch the developer's real one.
    callers = sorted(
        path.name
        for path in _python_files(TESTS_ROOT)
        if "default_database_path" in path.read_text(encoding="utf-8")
    )
    assert callers == [pathlib.Path(__file__).name]
