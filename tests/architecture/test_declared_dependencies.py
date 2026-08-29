"""A package must declare the third-party libraries it imports directly.

Two failure modes this catches, both of which were live in this repo:

1. **Undeclared but working.** ``rmspec-formats`` imported ``pydantic`` while
   declaring only ``rmspec-domain`` and ``rmscene``. It resolved transitively
   through the domain package, so nothing failed -- until the day the domain stops
   needing pydantic, at which point an unrelated package breaks.
2. **Declared but unused.** ``rmspec-export`` declared ``cairocffi`` with zero
   import sites, exactly like the legacy tree declared ``pillow`` in its
   ``[render]`` extra and imported ``PIL`` nowhere while a docstring promised a
   pillow fallback. An advertised dependency nothing uses is a lie about the
   package's requirements.

The architecture suite already asserts that a library is imported by the *right*
package (``test_dependency_direction``). This asserts the package's manifest and
its imports agree about what it needs.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"

#: Distribution name -> the module name(s) it provides, where they differ from the
#: distribution name. A distribution may provide more than one: ``pymupdf`` installs
#: both ``pymupdf`` and the legacy ``fitz`` alias, and either import satisfies it.
DIST_TO_MODULES: dict[str, frozenset[str]] = {
    "pillow": frozenset({"PIL"}),
    "pymupdf": frozenset({"pymupdf", "fitz"}),
    "pyobjc-framework-quartz": frozenset({"Quartz"}),
    "pyobjc-framework-vision": frozenset({"Vision"}),
    "pydantic-settings": frozenset({"pydantic_settings"}),
}


#: Imports that need no declaration: the standard library, and this workspace's own
#: packages (which are declared under their distribution names already).
def _stdlib() -> frozenset[str]:
    return frozenset(sys.stdlib_module_names)


def _members() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGES.iterdir() if p.is_dir())


def _declared(pkg: pathlib.Path) -> set[str]:
    data = tomllib.loads((pkg / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    specs = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies") or {}).values():
        specs.extend(extra)
    names = set()
    for spec in specs:
        # "pillow>=12.1.1; sys_platform == 'darwin'" -> "pillow"
        head = spec.split(";")[0].strip()
        name = head.split("[")[0]
        for op in (">=", "<=", "==", "!=", "~=", ">", "<"):
            name = name.split(op)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def _imported_top_level(pkg: pathlib.Path) -> set[str]:
    found: set[str] = set()
    for path in sorted((pkg / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def _modules_for(dist: str) -> frozenset[str]:
    return DIST_TO_MODULES.get(dist, frozenset({dist.replace("-", "_")}))


def _is_stub(pkg: pathlib.Path) -> bool:
    """Report whether a package has no implementation yet.

    Steps 4 to 6 of the restructure have not run, so rmspec-device, rmspec-ocr and
    rmspec-cli are scaffolding: an ``__init__.py`` and a ``py.typed`` marker. They
    legitimately declare the dependencies their future adapters will need, so the
    declared-but-unimported check would fire on all three for no fault. It
    reactivates on its own the moment a package grows real code.
    """
    modules = [f for f in (pkg / "src").rglob("*.py") if f.name != "__init__.py"]
    if modules:
        return False
    total = sum(
        len(f.read_text(encoding="utf-8").splitlines()) for f in (pkg / "src").rglob("*.py")
    )
    return total <= 10


@pytest.mark.parametrize("pkg", _members(), ids=lambda p: p.name)
def test_every_direct_import_is_declared(pkg: pathlib.Path) -> None:
    declared_modules = {m for d in _declared(pkg) for m in _modules_for(d)}
    # rmspec.* is this workspace; the distribution names are declared separately.
    undeclared = {
        mod
        for mod in _imported_top_level(pkg)
        if mod not in _stdlib()
        and mod != "rmspec"
        and mod not in declared_modules
        and not mod.startswith("_")
    }
    assert not undeclared, (
        f"{pkg.name} imports {sorted(undeclared)} directly but does not declare them. "
        f"They may resolve transitively today; that breaks the moment the package they "
        f"come through stops needing them. Declared: {sorted(_declared(pkg))}"
    )


@pytest.mark.parametrize("pkg", _members(), ids=lambda p: p.name)
def test_every_declared_third_party_is_imported(pkg: pathlib.Path) -> None:
    if _is_stub(pkg):
        pytest.skip(
            f"{pkg.name} has no implementation yet, so its declared dependencies are "
            f"forward-looking rather than unused. This check reactivates automatically "
            f"once the package grows a module beyond __init__.py."
        )
    imported = _imported_top_level(pkg)
    unused = {
        dist
        for dist in _declared(pkg)
        if not dist.startswith("rmspec-") and not (_modules_for(dist) & imported)
    }
    assert not unused, (
        f"{pkg.name} declares {sorted(unused)} but imports them nowhere under src/. "
        f"An advertised dependency nothing uses misstates the package's requirements -- "
        f"the legacy tree shipped exactly this with pillow, whose docstring promised a "
        f"fallback that did not exist."
    )
