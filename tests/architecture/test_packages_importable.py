"""Every member package is importable and declares an explicit public surface.

This is a smoke test with a real subject: the nine distributions share one PEP
420 ``rmspec`` namespace, each contributing ``rmspec.<name>`` via
``[tool.uv.build-backend] module-name`` + ``namespace = true``. That
configuration fails in a way plain unit tests never reach -- a stray
``rmspec/__init__.py`` in any member turns the namespace into a regular package
and shadows the other eight. So assert it directly.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"

MEMBERS = sorted(p.name.removeprefix("rmspec-") for p in PACKAGES.iterdir() if p.is_dir())


def test_members_were_discovered() -> None:
    assert len(MEMBERS) == 9, f"expected 9 member packages, found {MEMBERS}"


@pytest.mark.parametrize("name", MEMBERS)
def test_member_is_importable(name: str) -> None:
    module = importlib.import_module(f"rmspec.{name}")
    assert module.__file__ is not None
    assert f"rmspec-{name}" in module.__file__, (
        f"rmspec.{name} resolved to {module.__file__}, which is not inside "
        f"packages/rmspec-{name}/ -- the namespace mapping is wrong"
    )


@pytest.mark.parametrize("name", MEMBERS)
def test_member_declares_all(name: str) -> None:
    module = importlib.import_module(f"rmspec.{name}")
    assert hasattr(module, "__all__"), (
        f"rmspec.{name} has no __all__. Every member declares its public surface "
        f"explicitly, so that dead-code analysis can tell an export from an accident."
    )
    assert isinstance(module.__all__, list)


@pytest.mark.parametrize("name", MEMBERS)
def test_member_ships_inline_types(name: str) -> None:
    """PEP 561: a consumer's type checker must see our annotations."""
    marker = PACKAGES / f"rmspec-{name}" / "src" / "rmspec" / name / "py.typed"
    assert marker.is_file(), f"missing {marker.relative_to(REPO)}"


def test_namespace_has_no_init() -> None:
    """A single ``rmspec/__init__.py`` anywhere would shadow eight packages."""
    offenders = [p.relative_to(REPO) for p in PACKAGES.glob("*/src/rmspec/__init__.py")]
    assert not offenders, (
        f"rmspec must stay a PEP 420 namespace; found regular-package markers at: {offenders}"
    )
