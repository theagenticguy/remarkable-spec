"""Every test file must import under a module name no other test file claims.

This exists because of a real failure. The nine member packages each shipped a
``tests/__init__.py``, and so does the workspace root, so ``tests`` named ten
different directories at once. Under pytest's default ``prepend`` import mode a
file inside a package directory is imported by its dotted path from the first
ancestor *without* an ``__init__.py`` -- which made
``packages/rmspec-domain/tests/test_models.py`` resolve to ``tests.test_models``,
and ``tests`` was already bound to the root package. Eight test modules failed to
collect, and because a collection error is not a test failure, the coverage gate
reported 1% instead of failing outright on the missing suites.

The fix was to stop making the per-package ``tests/`` directories importable
packages: with no ``__init__.py`` they import by bare stem, which is unique.
That is a property no one would notice regressing until the next package grew a
suite, so it is asserted here rather than written down. Both halves of the rule
are checked -- no ``__init__.py`` under ``packages/*/tests/``, and no two test
files sharing a resolved module name -- because either one alone is satisfiable
while collection is still broken.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"


def _module_name(path: pathlib.Path) -> str:
    """Return the module name pytest's ``prepend`` import mode gives ``path``.

    Mirrors ``_pytest.pathlib.resolve_package_path``: walk up while each parent
    is a regular package, and the dotted name is everything walked over.
    """
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


def _test_files() -> list[pathlib.Path]:
    """Every collected test file, from both testpaths in pyproject.toml."""
    roots = [REPO / "tests", *sorted(PACKAGES.glob("*/tests"))]
    return sorted(p for root in roots if root.is_dir() for p in root.rglob("test_*.py"))


def test_test_files_were_discovered() -> None:
    """Guard the guard: an empty sweep would make the assertions below vacuous."""
    assert len(_test_files()) > 1, "found no test files to check for name collisions"


def test_no_two_test_modules_resolve_to_the_same_import_name() -> None:
    by_name: dict[str, list[pathlib.Path]] = defaultdict(list)
    for path in _test_files():
        by_name[_module_name(path)].append(path)
    collisions = {
        name: [str(p.relative_to(REPO)) for p in paths]
        for name, paths in sorted(by_name.items())
        if len(paths) > 1
    }
    assert not collisions, (
        "these test files import under the same module name, so pytest will "
        f"collect only one of each and silently skip the rest: {collisions}\n\n"
        "Rename one, or give its directory a unique name -- do not add "
        "__init__.py, which is what caused this in the first place."
    )


def test_no_package_test_directory_is_an_importable_package() -> None:
    offenders = [str(p.relative_to(REPO)) for p in sorted(PACKAGES.glob("*/tests/**/__init__.py"))]
    assert not offenders, (
        "a packages/*/tests/ directory declares itself a package, which makes its "
        f"modules import as `tests.<name>` and collide across packages: {offenders}\n\n"
        "Per-package test directories stay non-packages; the workspace-level "
        "tests/ tree is the only importable one."
    )
