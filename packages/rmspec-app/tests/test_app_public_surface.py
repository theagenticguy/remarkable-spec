"""The package's public surface, and the six conventions, as gates rather than prose.

Three agents are about to add use cases to this package in parallel. A convention that
lives only in :mod:`rmspec.app`'s docstring is a convention that drifts on the first
merge, so the ones that can be checked mechanically are checked here and apply to every
use case anyone adds:

* one class per use case, with keyword-only collaborators (convention 1);
* exactly one public method, taking one positional-only ``*Request`` and returning one
  ``*Result`` (convention 2);
* every result model frozen, ``extra="forbid"``, carrying a required
  ``degradations: tuple[Degradation, ...]`` (convention 3).

The surface assertions are deliberately *subset*-shaped rather than an exact list. An
exact ``__all__`` would make this file a merge conflict between three agents landing
disjoint modules, which trades a real invariant for a bookkeeping one. What is pinned
instead is every property that must hold however many names arrive: sorted, unique, no
private leakage, and complete re-export of every public module.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel

import rmspec.app
from rmspec.domain.errors import Degradation

FOUNDATION = frozenset(
    {
        "PageSelection",
        "ResolveDocument",
        "ResolveDocumentRequest",
        "ResolveDocumentResult",
    }
)

PRIVATE_MODULE_NAMES = frozenset({"DegradationLog"})


def _exported() -> dict[str, object]:
    return {name: getattr(rmspec.app, name) for name in rmspec.app.__all__}


def _exported_classes() -> dict[str, type]:
    return {name: obj for name, obj in _exported().items() if isinstance(obj, type)}


def _use_cases() -> dict[str, type]:
    """Return the exported classes that are not models: one per use case, by convention 1."""
    return {
        name: cls for name, cls in _exported_classes().items() if not issubclass(cls, BaseModel)
    }


def _models() -> dict[str, type[BaseModel]]:
    return {name: cls for name, cls in _exported_classes().items() if issubclass(cls, BaseModel)}


def _submodules() -> list[str]:
    return [module.name for module in pkgutil.iter_modules(rmspec.app.__path__)]


# ───────────────────────────── the surface itself ─────────────────────────────


def test_the_foundation_names_are_exported():
    assert set(rmspec.app.__all__) >= FOUNDATION


def test_all_is_sorted_and_free_of_repeats():
    assert rmspec.app.__all__ == sorted(set(rmspec.app.__all__))


def test_every_exported_name_resolves():
    for name in rmspec.app.__all__:
        assert hasattr(rmspec.app, name), name


def test_no_private_name_is_exported():
    assert [name for name in rmspec.app.__all__ if name.startswith("_")] == []


def test_no_module_object_is_exported():
    """``from rmspec.app import selection`` works; re-exporting the module does not."""
    offenders = [name for name, obj in _exported().items() if inspect.ismodule(obj)]
    assert offenders == []


def test_the_private_accumulator_is_not_part_of_the_surface():
    assert PRIVATE_MODULE_NAMES.isdisjoint(rmspec.app.__all__)


@pytest.mark.parametrize("module_name", _submodules())
def test_a_public_module_is_re_exported_completely(module_name: str):
    """A use case nobody can import from ``rmspec.app`` is a use case the CLI cannot bind."""
    module = importlib.import_module(f"rmspec.app.{module_name}")
    names = frozenset(getattr(module, "__all__", ()))
    if module_name.startswith("_"):
        assert names.isdisjoint(rmspec.app.__all__), (
            f"rmspec.app._{module_name.lstrip('_')} is private, so {sorted(names)} must not "
            f"appear in rmspec.app.__all__"
        )
        return
    missing = sorted(names - set(rmspec.app.__all__))
    assert not missing, (
        f"rmspec.app.{module_name} exports {missing}, which rmspec.app does not re-export. "
        f"Add them to __all__ in rmspec/app/__init__.py."
    )


def test_every_public_module_declares_an_all():
    for module_name in _submodules():
        if module_name.startswith("_"):
            continue
        module = importlib.import_module(f"rmspec.app.{module_name}")
        assert hasattr(module, "__all__"), module_name


# ───────────────── convention 1: one class, keyword-only collaborators ─────────────────


def test_there_is_at_least_one_use_case_to_check():
    """Guard the guard: an empty sweep would make every assertion below vacuous."""
    assert _use_cases()


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_a_use_case_takes_its_collaborators_keyword_only(name: str):
    parameters = list(inspect.signature(_use_cases()[name].__init__).parameters.values())
    positional = [
        parameter.name
        for parameter in parameters[1:]
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY
    ]
    assert positional == [], (
        f"{name} takes {positional} positionally. Collaborators are keyword-only, so "
        f"adding a seventh port cannot silently reorder a call site."
    )


#: Role suffixes, spelled out rather than caught by an ``-er``/``-or`` heuristic. That
#: heuristic reads ``ExtractDiagramsFromFolder`` as a role name, and a gate with a false
#: positive on a legitimate name is a gate the next agent deletes.
ROLE_SUFFIXES = (
    "Service",
    "Manager",
    "Helper",
    "Resolver",
    "Handler",
    "Processor",
    "Controller",
    "Coordinator",
    "Util",
    "Utils",
)


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_a_use_case_is_named_for_its_action(name: str):
    """Imperative, not a role: ``ResolveDocument``, never ``DocumentResolver``."""
    assert not name.endswith(ROLE_SUFFIXES), (
        f"{name} is named for a role rather than an action. A use case is the imperative "
        f"form of what it does, so the call site reads as a sentence."
    )


# ───────────────── convention 2: exactly one public method, one request ─────────────────


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_a_use_case_has_exactly_one_public_method(name: str):
    cls = _use_cases()[name]
    public = sorted(
        attribute
        for attribute, value in vars(cls).items()
        if not attribute.startswith("_") and callable(value)
    )
    assert len(public) == 1, (
        f"{name} has public methods {public}. One use case is one action, and a second "
        f"public method is a second use case wearing this one's name."
    )


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_the_public_method_takes_one_positional_only_request(name: str):
    cls = _use_cases()[name]
    (method_name,) = [
        attribute
        for attribute, value in vars(cls).items()
        if not attribute.startswith("_") and callable(value)
    ]
    parameters = list(inspect.signature(getattr(cls, method_name)).parameters.values())[1:]
    assert len(parameters) == 1, f"{name}.{method_name} takes {len(parameters)} arguments, not 1"
    (request,) = parameters
    assert request.kind is inspect.Parameter.POSITIONAL_ONLY
    assert str(request.annotation).endswith("Request"), request.annotation


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_the_public_method_returns_a_result_model(name: str):
    cls = _use_cases()[name]
    (method_name,) = [
        attribute
        for attribute, value in vars(cls).items()
        if not attribute.startswith("_") and callable(value)
    ]
    returns = inspect.signature(getattr(cls, method_name)).return_annotation
    assert str(returns).endswith("Result"), returns


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_a_use_case_is_not_callable_as_a_function(name: str):
    """A named method is greppable and reads at the call site; ``__call__`` is neither."""
    assert "__call__" not in vars(_use_cases()[name])


# ───────────────── convention 3: frozen results carrying their degradations ─────────────


@pytest.mark.parametrize("name", sorted(_models()))
def test_every_exported_model_is_frozen_and_forbids_extra_fields(name: str):
    config = _models()[name].model_config
    assert config.get("frozen") is True, name
    assert config.get("extra") == "forbid", name


@pytest.mark.parametrize("name", sorted(name for name in _models() if name.endswith("Result")))
def test_every_result_carries_its_degradations(name: str):
    field = _models()[name].model_fields.get("degradations")
    assert field is not None, f"{name} cannot report a substitution it made"
    assert field.annotation == tuple[Degradation, ...]
    assert field.is_required(), (
        f"{name}.degradations has a default, so a construction site can forget it"
    )


def test_there_is_at_least_one_result_model_to_check():
    assert [name for name in _models() if name.endswith("Result")]


@pytest.mark.parametrize("name", sorted(_use_cases()))
def test_each_use_case_has_a_request_and_a_result_of_its_own_name(name: str):
    exported = set(rmspec.app.__all__)
    assert f"{name}Request" in exported
    assert f"{name}Result" in exported
