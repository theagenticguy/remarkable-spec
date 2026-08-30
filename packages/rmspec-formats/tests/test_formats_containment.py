"""Structural guards: the parser is bound in one module and leaks nothing.

``tests/architecture/test_dependency_direction.py`` already asserts that no *other*
package imports ``rmscene``. These two assertions are the inside of that rule, and they
belong to this package because they are about its own shape: which of its modules may
name the parser, and that nothing parser-shaped reaches a caller.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

from rmspec import formats

SOURCE_ROOT = pathlib.Path(formats.__file__).parent
PARSER = "rmscene"
PARSER_OWNER = "scene_codec"

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""Every name an annotation mentions. Annotations here are strings, never type objects."""


def imported_modules(path: pathlib.Path) -> set[str]:
    """Return every top-level module name one file imports, function-local ones included."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return {name.split(".")[0] for name in found}


def test_the_source_tree_was_found():
    """Guard the guard: an empty sweep would make the assertions below vacuous."""
    assert len(list(SOURCE_ROOT.rglob("*.py"))) >= 6


def test_exactly_one_module_imports_the_parser():
    importers = {
        path.stem for path in sorted(SOURCE_ROOT.rglob("*.py")) if PARSER in imported_modules(path)
    }

    assert importers == {PARSER_OWNER}, (
        f"{PARSER} is bound at exactly one seam. Reach it through "
        f"rmspec.formats.{PARSER_OWNER}, or through the PageCodec port."
    )


def test_no_public_symbol_of_this_package_is_annotated_with_a_parser_type():
    """A parser type in an exported signature is the leak the port exists to prevent.

    Matched against the names the *defining module* binds to the parser rather than
    against the substring ``"rmscene"``. Every module here uses
    ``from __future__ import annotations``, so an annotation is a string: ``si.Group``
    and ``SceneTree`` are both parser types and neither contains the substring, so the
    substring test let them through. Alias matching is complete given
    :func:`test_exactly_one_module_imports_the_parser` -- a module that does not import
    the parser cannot name one resolvably.
    """
    offenders: list[str] = []
    for name in formats.__all__:
        exported = getattr(formats, name)
        for member, module_name, declared in _annotations_of(exported):
            aliases = _parser_aliases(module_name)
            offenders.extend(
                f"{name}.{member}({label}: {annotation})"
                for label, annotation in declared.items()
                if _annotation_names(annotation) & aliases
            )

    assert offenders == [], f"parser types reached the package surface: {offenders}"


def test_the_guard_can_see_a_parser_type_that_the_substring_test_could_not():
    """Guard the guard, over the exact two spellings that used to slip past."""
    assert PARSER not in "si.Group", "which is why the substring test was vacuous"
    assert PARSER not in "SceneTree"
    aliases = _parser_aliases(f"rmspec.formats.{PARSER_OWNER}")

    assert {"si", "SceneTree", "Block"} <= aliases
    assert _annotation_names("si.Group") & aliases
    assert _annotation_names("SceneTree | None") & aliases
    assert not _annotation_names("bytes | None") & aliases


def test_the_constructor_is_inspected_and_not_only_the_public_methods():
    """A vendor object reaches an adapter through its constructor, not through a method.

    ``dir()`` filtered on ``not member.startswith("_")``, so ``__init__`` -- which is
    where ``XochitlDocumentRepository`` takes its collaborators -- was never read by any
    assertion above.
    """
    inspected = {
        member: declared
        for member, _, declared in _annotations_of(formats.XochitlDocumentRepository)
    }

    assert "__init__" in inspected
    assert set(inspected["__init__"]) == {"root", "codec", "return"}, (
        "the constructor's annotations must reach the guard, not just exist"
    )


def _annotations_of(exported: object) -> list[tuple[str, str, dict[str, object]]]:
    """Return the annotations of one exported symbol, its constructor and its methods.

    Returns
    -------
    list[tuple[str, str, dict[str, object]]]
        One entry per inspected callable: its member name, the module that defined it --
        which is what the parser aliases are looked up in -- and its annotations.
    """
    if callable(exported) and not isinstance(exported, type):
        return [
            ("", getattr(exported, "__module__", ""), getattr(exported, "__annotations__", {}))
        ]
    members = [
        member
        for member in dir(exported)
        if not member.startswith("_") and callable(getattr(exported, member, None))
    ]
    return [
        (
            member,
            getattr(getattr(exported, member), "__module__", ""),
            getattr(getattr(exported, member), "__annotations__", {}),
        )
        for member in ["__init__", *members]
    ]


def _parser_aliases(module_name: str) -> frozenset[str]:
    """Return every name one module's namespace binds to something from the parser."""
    module = sys.modules.get(module_name)
    if module is None:
        return frozenset()
    return frozenset(
        name for name, value in vars(module).items() if _root_package(value) == PARSER
    )


def _root_package(value: object) -> str:
    """Return the top-level package a module or type came from, or the empty string."""
    origin = getattr(value, "__module__", None) or getattr(value, "__name__", None)
    return origin.split(".")[0] if isinstance(origin, str) else ""


def _annotation_names(annotation: object) -> frozenset[str]:
    """Return every identifier one annotation mentions, string or type object alike."""
    return frozenset(IDENTIFIER.findall(str(annotation)))


@pytest.mark.parametrize("name", sorted(formats.__all__))
def test_every_exported_name_exists_and_is_documented(name: str):
    exported = getattr(formats, name)

    assert exported.__doc__, f"{name} is part of the composition surface and needs a docstring"


def test_the_package_surface_is_exactly_the_composition_surface():
    """Four names: three adapters and one compatibility function.

    The legacy package re-exported nine free functions, six of which the domain now owns
    as ``decode`` classmethods. A use case reaches this package through a port, never by
    importing a function from it, so anything added here is a new dependency for a
    caller that had none.

    ``AppendOnlySceneWriter`` is the third adapter and the newest: it binds
    ``SceneAppender``, the write direction of the same parser seam. It earns a place here for
    the same reason the other two do -- a composition root has to name it -- and for no other,
    which is why nothing else about the encoder is exported.
    """
    assert sorted(formats.__all__) == [
        "AppendOnlySceneWriter",
        "SceneCodec",
        "XochitlDocumentRepository",
        "fingerprint_bytes",
    ]
