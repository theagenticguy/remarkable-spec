"""The package's public surface, pinned as an invariant rather than a convention.

``rmspec.ocr`` exports three adapters, the two names a container needs to decide it may build
them, and one client factory. Everything that knows a wire format or a native bridge lives behind
a leading underscore. That is held in place by nothing but module naming and a hand-written
``__all__``, and the obvious next change -- "let the CLI report which stop reason a page came back
with" -- would re-export ``rmspec.ocr._openai_wire.WireCompletion`` and put the envelope decoder's
own value type in the public surface with no gate failing.

Four properties matter more than tidiness here.

**No private module leaks.** ``_bedrock``, ``_openai_wire``, ``_confidence`` and
``_vision_framework`` are the modules that own provider knowledge. ``_openai_wire`` in particular
declares ``WireFormatError``, which is deliberately *not* a domain error and which
``rmspec.ocr.vision_model`` exists to translate -- so it must not be exported either.

**No double leaks.** ``rmspec.ocr.testing`` ships in the wheel, which is the point, but it is
imported explicitly and never re-exported, so a production import of this package cannot pull a
scripted adapter into a name a composition root might bind by accident. A bound double would
answer every page out of an empty dictionary and report the result as a reading.

**Importing the package must not need the ``vision`` extra.**
``rmspec.ocr._vision_framework`` imports ``Vision`` and ``Quartz`` at module scope, so importing
*it* fails outright on Linux. An ``__init__`` that reached it eagerly would make the whole
distribution unimportable on a CI runner -- and would do so while the package's own availability
check, whose entire job is to *report* the missing binding, sat inside it. That is asserted here
over the import graph rather than over ``sys.modules``, because this suite runs under
``pytest-randomly`` and ``pytest-xdist`` and ``test_ocr_apple_vision.py`` legitimately imports
that module on a machine that can.

**A dropped port stays dropped.** Two ports were considered and refused upstream --
``RecognizerEnsemble`` and three flavours of Mermaid validator -- and the reasons are recorded in
``ports/ocr.py`` and in this package's own ``__init__``. A docstring does not fail when someone
"fixes" the omission, so the vocabulary is checked instead.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from types import ModuleType

import pytest

import rmspec.ocr
import rmspec.ocr.testing
from rmspec.ocr import _bedrock

EXPECTED = [
    "AppleVisionRecognizer",
    "BdaRecognizer",
    "BedrockOpenAiVisionModel",
    "OcrEngine",
    "TextractRecognizer",
    "build_client",
    "require_backends",
]

EXPECTED_TESTING = [
    "DEFAULT_ENGINE_REVISION",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PROVIDER",
    "DEFAULT_REVISION",
    "FINGERPRINT_TAG",
    "ScriptedTextRecognizer",
    "ScriptedVisionLanguageModel",
]

#: The exported names that are classes. Split from the two callables below because "every entry
#: resolves to a class" is the assertion :mod:`rmspec.device` can make and this package cannot: a
#: composition root here needs one function to build a client and one to prove it may.
EXPECTED_CLASSES = frozenset(
    {
        "AppleVisionRecognizer",
        "BdaRecognizer",
        "BedrockOpenAiVisionModel",
        "OcrEngine",
        "TextractRecognizer",
    }
)

#: The exported names that are functions, and the whole judgement call this file pins.
EXPECTED_FUNCTIONS = frozenset({"build_client", "require_backends"})

#: ``from __future__ import annotations`` binds ``annotations`` as a module attribute. It is a
#: language artifact every module in the workspace carries, not something this package exports, so
#: it is named here rather than filtered by a broader rule that would also hide a real leak.
LANGUAGE_ARTIFACTS = {"annotations"}

#: Submodules a reader may import by name. The three adapter modules are public because a reader
#: reaching for ``PROVIDER``, ``DEFAULT_REVISION`` or ``ADAPTER_REVISION`` imports the module that
#: owns it; ``availability`` because ``EXTRA`` and ``FEATURE`` are the words a shell prints;
#: ``testing`` because the doubles ship. This is the ``rmspec.device.addresses`` precedent: a
#: public module deliberately kept out of ``__all__``, so every name in ``__all__`` stays something
#: a container binds or calls. It is a permitted set rather than a required one, because each of
#: these is bound on the package as a side effect of an import somewhere.
PUBLIC_MODULES = {
    "apple_vision",
    "availability",
    "bda",
    "testing",
    "textract",
    "vision_model",
}

#: Names the private modules define that must never become part of this surface. Spelled out
#: rather than read from each module's ``__all__`` for one specific reason: doing that would mean
#: importing ``_vision_framework``, and this file must stay collectable on a machine where that
#: import fails. ``_bedrock.build_client`` is deliberately absent -- it *is* exported, and the
#: reason is asserted below.
INTERNAL_NAMES = {
    "ACCEPTED_REASONING_EFFORTS",
    "ACCESS_CODES",
    "BACKEND",
    "BedrockRuntimeClient",
    "ClientFactory",
    "DEFAULT_FACTORY",
    "ENVELOPE",
    "REJECT_CODES",
    "RecognizedLine",
    "ResponseStream",
    "SERVICE",
    "THROTTLE_CODES",
    "UNAVAILABLE_CODES",
    "UNAVAILABLE_STATUSES",
    "VisionFrameworkError",
    "WIRE_REVISION",
    "WireCompletion",
    "WireFormatError",
    "build_body",
    "decode_body",
    "encode_request",
    "endpoint_for",
    "image_data_uri",
    "invoke",
    "joined_text",
    "lines_from_observations",
    "mean_character_confidence",
    "probe_backend",
    "recognize_lines",
    "translated",
}

#: What each exported class claims to be. A port binding claims its port's members plus whatever
#: factory builds it; the engine vocabulary claims its two members. Asserting the member set is
#: what makes "is what it claims" a test rather than a spelling check -- an ``__init__`` that
#: re-exported the wrong sibling would otherwise pass every other assertion in this file.
CLAIMED_MEMBERS = {
    "AppleVisionRecognizer": ("on_this_machine", "provider_id", "recognize"),
    # Two members and no factory of its own, which is the whole reason `build_client` needs an
    # entry in `__all__`: this binding's client is injected so the suite can drive it with a stub.
    "BedrockOpenAiVisionModel": ("complete", "fingerprint"),
    # `for_project` rather than `in_region`: this engine needs a project ARN as well as a
    # region, and the classmethod that takes both is the one a container calls.
    "BdaRecognizer": ("for_project", "provider_id", "recognize"),
    "OcrEngine": ("APPLE_VISION", "AWS_BDA", "AWS_TEXTRACT"),
    "TextractRecognizer": ("in_region", "provider_id", "recognize"),
}

#: What each shipped double claims. The port members, the seams that script them, and one counter.
CLAIMED_DOUBLE_MEMBERS = {
    "ScriptedTextRecognizer": ("fail", "provider_id", "read", "recognize", "recognize_calls"),
    "ScriptedVisionLanguageModel": (
        "answer",
        "complete",
        "complete_calls",
        "fail",
        "fingerprint",
        "model_id",
    ),
}

#: Words from the two ports this slice refused. ``ensemble`` because fan-out width and
#: partial-failure tolerance are use-case policy; the other three because Mermaid validity is a
#: Node toolchain, and no Python extra can supply an npm binary -- so its absence could not be
#: expressed as the "missing package, install this extra" composition failure this architecture
#: requires. ``ocr`` is not in this list and ``validate`` is, so a future `validate_backends` would
#: fail here and have to be named for what it does.
DROPPED_VOCABULARY = ("diagram", "ensemble", "linter", "mermaid", "validator")

PACKAGE_ROOT = pathlib.Path(rmspec.ocr.__file__ or "").parent
"""Where this package's source lives, for the two static assertions below."""

FRAMEWORK_MODULE = "rmspec.ocr._vision_framework"
"""The one module whose import can fail, and so the one nothing public may reach."""

ENTRY_POINTS = {
    "rmspec.ocr": "rmspec.ocr.apple_vision",
    "rmspec.ocr.testing": "rmspec.ocr.testing.doubles",
}
"""Every module a consumer imports by name, mapped to a module its closure must contain.

Both must be importable without the ``vision`` extra: the first is the package, and the second is
what an application-layer test binds. The value is a non-vacuity guard -- a resolver that silently
found nothing would satisfy "the framework module is not reachable" for either of them.

The two closures are computed independently and deliberately do not include each other. Importing
``rmspec.ocr.testing`` does of course run the parent's ``__init__`` at runtime, but that is a fact
about Python packages rather than an import statement, and the parent's own closure is the first
row here.
"""

MACOS_ONLY = frozenset({"Quartz", "Vision", "objc"})
"""The pyobjc bindings, which resolve on macOS only and only with the ``vision`` extra."""


def _public_attributes(module: ModuleType) -> dict[str, object]:
    """Return every attribute a reader would call part of ``module``'s surface.

    Parameters
    ----------
    module
        The module to inspect.

    Returns
    -------
    dict[str, object]
        Name to value, language artifacts and underscored names excluded.
    """
    return {
        name: getattr(module, name)
        for name in dir(module)
        if not name.startswith("_") and name not in LANGUAGE_ARTIFACTS
    }


def _public_values(module: ModuleType) -> dict[str, object]:
    """Return the public attributes that are not submodules.

    Submodules are filtered by *type* rather than by name because they are bound on the package as
    a side effect of whatever has been imported in this interpreter -- and this suite runs under
    ``pytest-randomly`` and ``pytest-xdist``, so a name-based filter would make the surface
    assertion depend on collection order.

    Parameters
    ----------
    module
        The module to inspect.

    Returns
    -------
    dict[str, object]
        Name to value, submodules excluded.
    """
    return {
        name: value
        for name, value in _public_attributes(module).items()
        if not isinstance(value, ModuleType)
    }


def _is_type_checking(test: ast.expr) -> bool:
    """Report whether an ``if`` guard is the ``TYPE_CHECKING`` one.

    Parameters
    ----------
    test
        The condition of an ``ast.If``.

    Returns
    -------
    bool
        ``True`` for ``TYPE_CHECKING`` and ``typing.TYPE_CHECKING``. A body under that guard never
        executes, so an import inside one cannot make a package unimportable.
    """
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _imports_in(body: list[ast.stmt]) -> set[str]:
    """Return every module named by an import that runs when ``body`` is executed.

    Descends into ``if`` and ``try`` blocks, because a module-scope
    ``try: import X / except ImportError:`` is a real pattern and its import does run. Does *not*
    descend into a function or class body, whose imports run only when something calls it -- which
    is exactly how this package keeps the macOS-only module out of its import graph.

    Parameters
    ----------
    body
        A statement list at module scope, or at the scope of a block inside one.

    Returns
    -------
    set[str]
        Candidate module names. A ``from a.b import c`` yields both ``a.b`` and ``a.b.c``, since
        ``c`` may be a submodule and the caller resolves which of the two is a file.
    """
    found: set[str] = set()
    for node in body:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.If) and not _is_type_checking(node.test):
            found |= _imports_in(node.body) | _imports_in(node.orelse)
        elif isinstance(node, ast.Try):
            found |= _imports_in(node.body) | _imports_in(node.orelse)
            found |= _imports_in(node.finalbody)
            for handler in node.handlers:
                found |= _imports_in(handler.body)
    return found


def _every_import(path: pathlib.Path) -> set[str]:
    """Return every module one file imports, function-local imports included.

    Parameters
    ----------
    path
        A source file in this package.

    Returns
    -------
    set[str]
        Top-level module names, so ``import Quartz`` and ``from Quartz import x`` both surface.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _path_for(module: str) -> pathlib.Path | None:
    """Resolve a dotted module name inside this package to its source file.

    Parameters
    ----------
    module
        A dotted name beginning ``rmspec.ocr``.

    Returns
    -------
    pathlib.Path | None
        The file, or ``None`` when the name is not a module of this package -- which is how a
        ``from rmspec.ocr.testing.doubles import ScriptedTextRecognizer`` candidate is discarded.
    """
    suffix = module.removeprefix("rmspec.ocr").lstrip(".")
    base = PACKAGE_ROOT.joinpath(*suffix.split(".")) if suffix else PACKAGE_ROOT
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _reachable_from(entry: str) -> set[str]:
    """Return every module of this package that importing ``entry`` executes.

    Parameters
    ----------
    entry
        A dotted module name inside this package.

    Returns
    -------
    set[str]
        The transitive closure over module-scope imports, ``entry`` included.
    """
    seen = {entry}
    pending = [entry]
    while pending:
        path = _path_for(pending.pop())
        assert path is not None, "every module in the closure resolves to a file"
        for candidate in _imports_in(ast.parse(path.read_text(encoding="utf-8")).body):
            if (
                candidate.startswith("rmspec.ocr")
                and candidate not in seen
                and _path_for(candidate) is not None
            ):
                seen.add(candidate)
                pending.append(candidate)
    return seen


def _declared_names() -> set[str]:
    """Return every name this package declares public, across the package and its submodules.

    Returns
    -------
    set[str]
        The union of ``__all__`` for the package and each of its public modules. Only the public
        modules, because reading the private ones' would mean importing ``_vision_framework``.
    """
    names = set(rmspec.ocr.__all__)
    for module in sorted(PUBLIC_MODULES):
        names |= set(getattr(rmspec.ocr, module).__all__)
    return names


# ── the surface is exactly what __all__ declares ────────────────────────────────────────


def test_all_is_sorted_and_is_exactly_the_expected_set() -> None:
    assert rmspec.ocr.__all__ == sorted(rmspec.ocr.__all__)
    assert rmspec.ocr.__all__ == EXPECTED


def test_the_public_surface_is_exactly_what_all_declares() -> None:
    assert sorted(_public_values(rmspec.ocr)) == EXPECTED


def test_every_exported_name_exists_and_is_the_kind_of_thing_it_is_listed_as() -> None:
    """``__all__`` entries that do not exist fail only on ``import *``, which nobody writes."""
    assert set(EXPECTED) == EXPECTED_CLASSES | EXPECTED_FUNCTIONS
    for name in sorted(EXPECTED_CLASSES):
        assert isinstance(getattr(rmspec.ocr, name), type)
    for name in sorted(EXPECTED_FUNCTIONS):
        assert inspect.isfunction(getattr(rmspec.ocr, name))


@pytest.mark.parametrize(("name", "members"), sorted(CLAIMED_MEMBERS.items()))
def test_every_exported_class_is_what_it_claims(name: str, members: tuple[str, ...]) -> None:
    bound = getattr(rmspec.ocr, name)
    public = tuple(sorted(member for member in vars(bound) if not member.startswith("_")))
    assert public == members


def test_no_private_module_leaks_into_the_public_surface() -> None:
    surfaced = {
        name
        for name, value in _public_attributes(rmspec.ocr).items()
        if isinstance(value, ModuleType)
    }
    assert surfaced <= PUBLIC_MODULES

    private = {
        name
        for name, value in vars(rmspec.ocr).items()
        if name.startswith("_") and isinstance(value, ModuleType)
    }
    # Guard the guard: with no private submodules bound the assertion below is vacuous.
    assert private
    assert private.isdisjoint(rmspec.ocr.__all__)


def test_no_internal_seam_is_re_exported() -> None:
    assert INTERNAL_NAMES.isdisjoint(rmspec.ocr.__all__)
    assert INTERNAL_NAMES.isdisjoint(_public_values(rmspec.ocr))


# ── the client factories, which is the judgement call ───────────────────────────────────


def test_the_two_per_adapter_factories_arrive_with_the_classes_that_own_them() -> None:
    """Neither needs an ``__all__`` entry, because a classmethod is not a module-level name."""
    for owner, factory in (
        (rmspec.ocr.TextractRecognizer, "in_region"),
        (rmspec.ocr.AppleVisionRecognizer, "on_this_machine"),
    ):
        assert isinstance(inspect.getattr_static(owner, factory), classmethod)
        assert factory not in rmspec.ocr.__all__
        assert factory not in _public_values(rmspec.ocr)


def test_the_client_factory_is_exported_because_it_has_no_public_owner() -> None:
    """The model's client is injected, so the factory has no exported class to arrive with."""
    # Re-exported by identity from the private module that owns it, which is the narrowest
    # widening available: `_bedrock` is also where every botocore exception is caught, so it stays
    # private, and this `__init__` is therefore the factory's only public address.
    assert rmspec.ocr.build_client is _bedrock.build_client

    # And this is the fact that makes the entry necessary rather than a convenience. If the model
    # ever grows an `in_region`-style classmethod, this assertion fails and the export can go.
    owned = [
        name
        for name, value in vars(rmspec.ocr.BedrockOpenAiVisionModel).items()
        if isinstance(value, classmethod)
    ]
    assert owned == []


def test_the_composition_check_and_its_vocabulary_are_both_reachable() -> None:
    """A function whose argument type is unexported is a function no container can call."""
    assert callable(rmspec.ocr.require_backends)
    assert set(rmspec.ocr.OcrEngine) == {
        rmspec.ocr.OcrEngine.APPLE_VISION,
        rmspec.ocr.OcrEngine.AWS_BDA,
        rmspec.ocr.OcrEngine.AWS_TEXTRACT,
    }


# ── the doubles ship and are never re-exported ──────────────────────────────────────────


def test_the_doubles_are_not_re_exported_by_the_package() -> None:
    """A production import must not pull a scripted adapter into a bindable name."""
    assert "testing" not in rmspec.ocr.__all__
    assert set(EXPECTED_TESTING).isdisjoint(_public_values(rmspec.ocr))


def test_the_testing_subpackage_has_the_same_kind_of_pinned_surface() -> None:
    assert rmspec.ocr.testing.__all__ == sorted(rmspec.ocr.testing.__all__)
    assert rmspec.ocr.testing.__all__ == EXPECTED_TESTING
    assert sorted(_public_values(rmspec.ocr.testing)) == EXPECTED_TESTING


@pytest.mark.parametrize(("name", "members"), sorted(CLAIMED_DOUBLE_MEMBERS.items()))
def test_every_shipped_double_is_what_it_claims(name: str, members: tuple[str, ...]) -> None:
    bound = getattr(rmspec.ocr.testing, name)
    public = tuple(sorted(member for member in vars(bound) if not member.startswith("_")))
    assert public == members


# ── importing the package must not need the vision extra ────────────────────────────────


def test_the_framework_module_is_in_this_package_and_names_the_macos_only_bindings() -> None:
    """Guard the guard: the two assertions below are vacuous if it is not there to be reached."""
    path = _path_for(FRAMEWORK_MODULE)
    assert path is not None
    assert MACOS_ONLY & _every_import(path)


def test_only_the_framework_module_names_the_macos_only_bindings() -> None:
    named = sorted(
        path.name for path in PACKAGE_ROOT.rglob("*.py") if MACOS_ONLY & _every_import(path)
    )
    assert named == ["_vision_framework.py"]


@pytest.mark.parametrize(("entry", "must_reach"), sorted(ENTRY_POINTS.items()))
def test_nothing_reachable_from_an_entry_point_imports_the_framework_module(
    entry: str,
    must_reach: str,
) -> None:
    # The property that makes `import rmspec.ocr` work on a Linux runner. Both names that use the
    # framework -- `AppleVisionRecognizer.on_this_machine` and `require_backends` -- load it by
    # name inside the call that needs it, which is what lets the second one *report* a missing
    # binding rather than merely be one.
    reachable = _reachable_from(entry)
    assert FRAMEWORK_MODULE not in reachable
    # Guard the guard again: a resolver that silently found nothing would pass the line above.
    assert must_reach in reachable


def test_the_entry_points_reach_every_module_that_is_not_the_framework() -> None:
    """Which is what makes the exclusion above a statement about one module and not about six."""
    on_disk = {
        f"rmspec.ocr.{path.stem}" if path.name != "__init__.py" else "rmspec.ocr"
        for path in PACKAGE_ROOT.glob("*.py")
    }
    assert _reachable_from("rmspec.ocr") == on_disk - {FRAMEWORK_MODULE}


# ── a dropped port stays dropped ────────────────────────────────────────────────────────


def test_no_refused_port_has_quietly_reappeared() -> None:
    surfaced = sorted(
        name
        for name in _declared_names()
        if any(word in name.casefold() for word in DROPPED_VOCABULARY)
    )
    assert surfaced == []
    # Guard the guard: a vocabulary that matched nothing anywhere would also pass.
    assert any(word in "MermaidValidator".casefold() for word in DROPPED_VOCABULARY)
