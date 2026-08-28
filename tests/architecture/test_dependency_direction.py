"""Architecture invariants, enforced rather than documented.

The layering in the old single-distribution layout was clean and acyclic, and
nothing checked it -- so a technology detail could leak into a caller that had
no business knowing about it, and did. This module is the check.

Two families of assertion:

1. **Internal direction.** Which `rmspec.*` packages a package may import.
   `rmspec.app` may import `rmspec.domain` and nothing else; that single edge is
   what makes the use cases testable with plain fakes.
2. **Third-party containment.** Which package owns each native or cloud
   dependency. Ruff's ``flake8-tidy-imports.banned-api`` covers the three worst
   (``sqlite3``, ``boto3``, ``paramiko``) at lint time; this covers all of them
   at test time and, unlike the lint rule, reports the whole violating set at
   once instead of failing on the first file.
"""

from __future__ import annotations

import ast
import pathlib
from collections import defaultdict

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"

#: Workspace-internal edges. Key may import exactly the values, transitively
#: closed by the reader -- ``export`` importing ``render`` does not license
#: ``export`` to import what ``render`` imports beyond ``domain``.
ALLOWED_INTERNAL: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "app": frozenset({"domain"}),
    "formats": frozenset({"domain"}),
    "render": frozenset({"domain"}),
    "export": frozenset({"domain", "render"}),
    "device": frozenset({"domain"}),
    "ocr": frozenset({"domain"}),
    "persistence": frozenset({"domain"}),
    "cli": frozenset(
        {"domain", "app", "formats", "render", "export", "device", "ocr", "persistence"}
    ),
}

#: Third-party top-level module -> the one package allowed to import it.
#: Anything absent from this map is unrestricted (pydantic, stdlib, typing).
OWNED_THIRD_PARTY: dict[str, str] = {
    "rmscene": "formats",
    "sqlite3": "persistence",
    "boto3": "ocr",
    "botocore": "ocr",
    "Vision": "ocr",
    "Quartz": "ocr",
    "objc": "ocr",
    "httpx": "device",
    "paramiko": "device",
    "cairocffi": "export",
    "cairosvg": "export",
    "PIL": "export",
    "fitz": "export",
    "pymupdf": "export",
    "cyclopts": "cli",
    "rich": "cli",
    "dishka": "cli",
    "pydantic_settings": "cli",
    "markdown": "cli",
    "weasyprint": "cli",
}


def _packages() -> list[str]:
    return sorted(p.name.removeprefix("rmspec-") for p in PACKAGES.iterdir() if p.is_dir())


def _source_files(package: str) -> list[pathlib.Path]:
    root = PACKAGES / f"rmspec-{package}" / "src"
    return sorted(root.rglob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Top-level module names imported by one file, including inside functions.

    ``ast.walk`` rather than a top-level scan on purpose: the old codebase used
    function-local imports throughout to keep optional extras out of CLI
    startup, and a containment check that only looked at module scope would have
    missed every one of them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _internal_edges() -> dict[str, set[tuple[str, pathlib.Path]]]:
    """Map each package to the (imported_package, citing_file) pairs it declares."""
    edges: dict[str, set[tuple[str, pathlib.Path]]] = defaultdict(set)
    for package in _packages():
        for path in _source_files(package):
            for module in _imported_modules(path):
                parts = module.split(".")
                if len(parts) >= 2 and parts[0] == "rmspec":
                    target = parts[1]
                    if target != package:
                        edges[package].add((target, path.relative_to(REPO)))
    return edges


def test_every_package_has_an_allowed_edge_set() -> None:
    """A new package must declare its allowed imports before it can be merged."""
    assert set(_packages()) == set(ALLOWED_INTERNAL), (
        "packages/ and ALLOWED_INTERNAL disagree. Add the new package's allowed "
        "edge set here -- an unlisted package would otherwise be unconstrained."
    )


@pytest.mark.parametrize("package", sorted(ALLOWED_INTERNAL))
def test_internal_imports_stay_within_allowed_edges(package: str) -> None:
    allowed = ALLOWED_INTERNAL[package]
    edges = sorted(_internal_edges().get(package, set()), key=lambda e: (e[1], e[0]))
    violations = [
        f"  {path}: rmspec.{package} -> rmspec.{target}"
        for target, path in edges
        if target not in allowed
    ]
    assert not violations, (
        f"rmspec.{package} may import {sorted(allowed) or 'nothing'}, but imports:\n"
        + "\n".join(violations)
    )


def test_app_layer_imports_domain_only() -> None:
    """The single most important edge, asserted on its own so it fails loudly.

    ``rmspec.app`` holds the use cases. If it can reach an adapter, the adapter's
    third-party dependency becomes a dependency of every test that touches a use
    case, and the ports stop earning their keep.
    """
    targets = {target for target, _ in _internal_edges().get("app", set())}
    extra = sorted(targets - {"domain"})
    assert not extra, f"rmspec.app must import rmspec.domain only; it also imports {extra}"


def test_no_import_cycles_between_packages() -> None:
    edges = {pkg: {t for t, _ in pairs} for pkg, pairs in _internal_edges().items()}
    cycles = {
        f"rmspec.{package} <-> rmspec.{target}"
        for package, targets in edges.items()
        for target in targets
        if package in edges.get(target, set())
    }
    assert not cycles, f"import cycles: {sorted(cycles)}"


@pytest.mark.parametrize(("module", "owner"), sorted(OWNED_THIRD_PARTY.items()))
def test_third_party_dependency_stays_in_its_package(module: str, owner: str) -> None:
    violations = [
        f"  {path.relative_to(REPO)} (in rmspec-{package})"
        for package in _packages()
        if package != owner
        for path in _source_files(package)
        if any(m == module or m.startswith(f"{module}.") for m in _imported_modules(path))
    ]
    assert not violations, (
        f"`{module}` belongs to rmspec-{owner} only, but is imported by:\n"
        + "\n".join(sorted(violations))
        + f"\n\nGo through a port in rmspec.domain.ports instead of importing {module} directly."
    )
