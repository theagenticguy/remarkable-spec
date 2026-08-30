"""``rmspec manifest`` -- the command that ends ``--help`` scraping.

One JSON document describing the whole CLI: every command with its parameters and **every**
discriminator its ``data`` can carry, every error identity with its exit code, the closed set
of degradation kinds, and every ``RMSPEC_*`` setting. Derived by introspection from the objects
that already exist -- the cyclopts ``App``, ``RmspecError``'s subclass tree,
:class:`~rmspec.domain.errors.DegradationKind`, and ``CliSettings.model_fields`` -- so it cannot
drift from the surface it describes the way a hand-written table would.

This is also what replaces a parallel ``ERR_*`` enum. That pattern exists so an agent can
discover the closed set of failure identities it may have to branch on; a hand-maintained mirror
of a 48-class tree would disagree with the tree the first time someone added a leaf, while the
``errors`` section here is the tree.

What is introspected, and the one private attribute
---------------------------------------------------
``cyclopts.Argument`` and ``cyclopts.ArgumentCollection`` are public exports and carry everything
a parameter description needs: ``names`` (positive *and* negative), ``hint``, ``required``,
``field_info.default``, ``get_choices(force=True)`` for a ``Literal`` or an ``Enum``, and
``is_flag()``. ``App.assemble_argument_collection(parse_docstring=True)`` is public and is what
joins a function's signature to its numpydoc ``Parameters`` section, so the manifest's help text
and ``--help``'s are the same string by construction.

The one thing with no public equivalent is **registration order**, which is the order ``--help``
prints and the only ordering a reader of this document would recognise. ``App._commands`` is the
ordered mapping behind ``__iter__``, ``__contains__`` and ``resolved_commands()``, so the whole
public surface already funnels through it. :func:`_registrations` is the only function in this
package that touches it, it names the attribute in one constant, and
``test_cli_manifest.py`` pins both -- because a cyclopts upgrade that renamed it must fail the
build loudly rather than quietly emit a manifest with no commands in it. A manifest that
under-reports is worse than no manifest.

Each key from that mapping is then resolved through the **public** ``App.__getitem__``, which
imports a lazily registered command's module. That import is unavoidable and not a regression:
a complete manifest needs each command's signature, and the signature lives in the module. Lazy
loading and full self-description are in tension, and self-description wins in the one command
whose entire job it is.

Three traps this walker avoids, each measured
---------------------------------------------
``App.command()`` registers **the same App object under every name and alias**, so the
CLI-visible name is the mapping's **key** and never ``subapp.name[0]`` -- a function ``ocr_run``
registered as ``name="run"`` reports ``name == ('ocr-run',)``. Aliases are therefore folded by
object identity. ``App.subapps`` is public but yields one entry per alias plus the internal help
and version apps, so it is the wrong tool. And ``app.version`` is a **callable** in this CLI, so
anything that serialises it without calling it emits ``"<function _version at 0x...>"``; a test
asserts that no value anywhere in the document contains ``0x``.

Errors are scored through the domain's own function, never a copy of its table
-----------------------------------------------------------------------------
:func:`~rmspec.domain.errors.exit_code` takes an instance and walks ``type(err).__mro__`` against
a deliberately sparse private mapping. Several classes have required keyword fields, and
inventing plausible values for them to score a class would put fiction in the manifest. So each
class is scored through ``cls.__new__(cls)``: an instance with no ``__init__`` run and no field
values at all, whose only purpose is to carry its own ``__mro__`` into the domain's walk. The
sparseness is why ``abstract`` is reported rather than dropped -- an agent may see a concrete
name whose exit code came from an ancestor.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Any, Final, Literal, Union, get_args, get_origin

from rmspec.cli._invoke import DenseFlag, JsonFlag, render
from rmspec.cli._output import CliOutput, OutputMode
from rmspec.cli._settings import CliSettings
from rmspec.cli._skill import SKILL_FLAGS, describe_skill
from rmspec.domain import errors

if TYPE_CHECKING:
    from cyclopts import App, Argument

__all__ = [
    "MANIFEST_RESPONSE_TYPE",
    "RESPONSE_TYPES",
    "SETTING_PREFIX",
    "build_manifest",
    "manifest",
    "response_types",
]

MANIFEST_RESPONSE_TYPE: Final = "manifest"
"""This command's own ``type`` discriminator, so the manifest wears the envelope it describes."""

SETTING_PREFIX: Final = "RMSPEC_"
"""The prefix every setting's environment variable carries, restated for the manifest's names.

``CliSettings.model_config`` owns the real one; this is the spelling the ``settings`` section
prints, and a test asserts the two agree so the manifest cannot advertise an unreadable name.
"""

_ENTRY_MODULE: Final = "rmspec.cli"
"""Where the root ``App`` lives, reached by string because importing it directly is a cycle.

``__init__.py`` imports this module in order to register the command, so a module-scope
``from rmspec.cli import app`` here would be a circular import. By the time the command body
runs, ``rmspec.cli`` is fully imported and the attribute is there.
"""

_COMMANDS_ATTRIBUTE: Final = "_commands"
"""The one private cyclopts attribute this package reads, named once so a test can pin it.

``App._commands`` maps a CLI name to an ``App`` or, for a lazily registered command, to a
``CommandSpec``. Read for registration order; see this module's docstring.
"""

_SYNC_MODULE: Final = "rmspec.cli._sync"
"""Where the ``history`` discriminator lives, reached by string because a direct import cycles.

``_sync.py`` imports :data:`RESPONSE_TYPES` from this module at module scope, so a module-scope
``from rmspec.cli._sync import HISTORY_RESPONSE_TYPE`` here would fail on a half-initialised
``_manifest``. Read at call time instead, when both modules are fully imported. The value is
read rather than retyped for the same reason every command reads :data:`RESPONSE_TYPES`: two
spellings of one discriminator drift, and the manifest is the document a caller trusts.
"""

RESPONSE_TYPES: Final = {
    "ls": "catalog",
    "read": "document",
    "render": "render",
    "ocr": "transcription",
    "diagram": "diagrams",
    "annotations": "annotations",
    "search": "matches",
    "sync": "sync",
    "push": "created",
    "reply": "reply",
    "device info": "facts",
    "doctor": "capabilities",
    "env": "settings",
    "manifest": MANIFEST_RESPONSE_TYPE,
}
"""Every command's **primary** ``type`` discriminator, keyed by the invocation a user types.

The one part of a command's description that cyclopts cannot supply, because it is a fact about
the *result* rather than about the signature. Keyed by the space-joined invocation, so
``device info`` is one entry rather than a rule about nesting.

An agent reads this before it calls: knowing ``rmspec ocr`` answers ``transcription`` is what
lets it pick the parser for ``data`` in advance rather than sniffing the document afterwards.

This table is one value per command because a command body needs exactly one when it emits.
The *manifest* publishes :func:`response_types` instead, which is a list, because a flag can
select a second shape -- see that function.

A test asserts every *registered* command has an entry here, so a command that shipped without
one fails the build instead of emitting ``"response_types": null``.
"""

_PARAMETER_KINDS: Final = {
    "var_positional": "var_positional",
    "positional": "positional",
    "keyword": "keyword",
    "positional_or_keyword": "positional_or_keyword",
}
"""The four kinds a parameter description may report, spelled once.

A mapping rather than four literals so that the vocabulary is enumerable: a consumer that wants
to know which kinds exist can read this, and a test can assert the walker never invents a fifth.
"""


def _flag_selected_response_types() -> dict[str, tuple[str, ...]]:
    """List the extra discriminators a flag can select, per invocation.

    Returns
    -------
    dict[str, tuple[str, ...]]
        Invocation to the discriminators it emits **in addition** to its
        :data:`RESPONSE_TYPES` entry, in the order a reader should meet them.

    Notes
    -----
    One entry today: ``sync --history`` answers ``history`` rather than ``sync``, because it
    calls a different use case and returns a different result type. Read from
    ``_sync.HISTORY_RESPONSE_TYPE``, which already owns that string, through
    :data:`_SYNC_MODULE` -- a module-scope import would be a cycle.

    A function rather than a constant for exactly that reason: the import has to happen after
    both modules are loaded. ``importlib`` caches, so the cost is a dictionary lookup.
    """
    return {"sync": (str(importlib.import_module(_SYNC_MODULE).HISTORY_RESPONSE_TYPE),)}


def response_types(invocation: str, /) -> list[str] | None:
    """List every ``type`` discriminator one invocation can put in a success envelope.

    Parameters
    ----------
    invocation
        The space-joined words a user types after ``rmspec``, as :data:`RESPONSE_TYPES` keys
        them: ``"ls"``, ``"device info"``.

    Returns
    -------
    list[str] | None
        The primary discriminator first, then any a flag selects; ``None`` for an invocation
        the table does not know.

    Notes
    -----
    A **list**, and the plural is the whole point. The manifest exists so that an agent knows
    the shape of ``data`` *before* it calls, and ``rmspec sync --history`` emits
    ``type: "history"`` while ``RESPONSE_TYPES["sync"]`` is ``"sync"``. A single-valued field
    would therefore under-report a discriminator a caller really receives, which is the one
    failure a self-describing surface must not have. ``["catalog"]`` for a command with one
    shape is the honest spelling of the same fact.
    """
    primary = RESPONSE_TYPES.get(invocation)
    if primary is None:
        return None
    return [primary, *_flag_selected_response_types().get(invocation, ())]


def _jsonable(value: object, /) -> object:
    """Reduce one introspected value to something ``json`` can write.

    Parameters
    ----------
    value
        A default, a choice, or a setting value.

    Returns
    -------
    object
        A ``str``, number, ``bool``, ``None``, or a list of those.

    Notes
    -----
    ``Enum`` before ``str`` deliberately: a ``StrEnum`` member *is* a ``str``, and while
    ``json`` would write its value anyway, going through ``.value`` states the intent and keeps
    a non-string ``Enum`` from falling through to ``str(value)`` as ``"Feature.RASTER"``.
    A set is sorted, because a manifest that reorders itself between two runs of the same
    build cannot be diffed.
    """
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, frozenset | set):
        return sorted(str(_jsonable(item)) for item in value)
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _generic_type_name(origin: object, arguments: tuple[object, ...], /) -> str:
    """Name a parameterised type from its origin and its arguments.

    Parameters
    ----------
    origin
        ``get_origin(hint)``, already known not to be ``None``.
    arguments
        ``get_args(hint)``.

    Returns
    -------
    str
        ``"Literal['a', 'b']"``, ``"Path | None"``, ``"frozenset[OcrEngineName]"``.

    Notes
    -----
    A ``Literal``'s arguments are *values* rather than types, so they are ``repr``'d rather
    than recursed into -- ``Literal['a']`` and ``Literal[a]`` describe different surfaces.
    Both spellings of a union are handled: ``X | None`` has ``types.UnionType`` for an origin
    and ``Optional[X]`` has ``typing.Union``, and a settings field may be written either way.
    """
    if origin is Literal:
        return f"Literal[{', '.join(repr(value) for value in arguments)}]"
    if origin is UnionType or origin is Union:
        return " | ".join(_type_name(argument) for argument in arguments)
    return f"{_type_name(origin)}[{', '.join(_type_name(a) for a in arguments)}]"


def _type_name(hint: object, /) -> str:
    """Name a type the way a reader would write it.

    Parameters
    ----------
    hint
        ``Argument.hint``, or a settings field's annotation.

    Returns
    -------
    str
        ``"str"``, ``"Path | None"``, ``"tuple[str, ...]"``, ``"frozenset[OcrEngineName]"``,
        ``"Literal['a', 'b']"``.

    Notes
    -----
    Rebuilt from ``get_origin``/``get_args`` rather than printed with ``str``, because ``str``
    qualifies every name with its defining module: ``Path | None`` prints as
    ``"pathlib._local.Path | None"``, naming a private standard-library module, and
    ``frozenset[OcrEngineName]`` prints its member as
    ``"rmspec.cli._settings.OcrEngineName"``. Neither is a type a caller could write, and the
    first would put an implementation detail of the interpreter into a published contract.

    ``NoneType`` is rendered ``None`` because that is the spelling in the annotation it came
    from, and ``Ellipsis`` is rendered ``...`` for the same reason -- a homogeneous
    ``tuple[str, ...]`` is not ``tuple[str, Ellipsis]`` to anyone reading it.
    """
    if hint is None or hint is type(None):
        return "None"
    if hint is Ellipsis:
        return "..."
    if isinstance(hint, type):
        return hint.__name__
    origin = get_origin(hint)
    if origin is None:
        return str(hint).replace("typing.", "")
    return _generic_type_name(origin, get_args(hint))


def _parameter_kind(argument: Argument, /) -> str:
    """Classify one parameter as the CLI presents it.

    Parameters
    ----------
    argument
        The cyclopts argument.

    Returns
    -------
    str
        One of :data:`_PARAMETER_KINDS`' values.
    """
    if argument.is_var_positional():
        return _PARAMETER_KINDS["var_positional"]
    if argument.is_positional_only():
        return _PARAMETER_KINDS["positional"]
    if argument.field_info.is_keyword_only:
        return _PARAMETER_KINDS["keyword"]
    return _PARAMETER_KINDS["positional_or_keyword"]


def _describe_parameter(argument: Argument, /) -> dict[str, Any]:
    """Describe one parameter of one command.

    Parameters
    ----------
    argument
        The cyclopts argument, from a collection already filtered to the help-visible ones.

    Returns
    -------
    dict[str, Any]
        ``name`` is the Python identifier and ``flags`` the CLI spellings, because those are
        different vocabularies and a caller needs both: ``flags`` is what it types, ``name``
        is what the ``--help`` text and the function signature call it. ``flags`` carries the
        negative spellings too -- a ``--no-x`` cyclopts generated is part of the surface
        whether or not anyone meant it to be, and a manifest that hid it would let the CLI
        accept a flag the manifest denies.
    """
    info = argument.field_info
    has_default = info.default is not info.empty
    choices = argument.get_choices(force=True)
    return {
        "name": info.name,
        "flags": list(argument.names),
        "negative_flags": list(argument.negatives),
        "kind": _parameter_kind(argument),
        "type": _type_name(argument.hint),
        "required": argument.required,
        "default": _jsonable(info.default) if has_default else None,
        "choices": list(choices) if choices else None,
        "is_flag": argument.is_flag(),
        "help": argument.parameter.help or info.help,
    }


def _modes(command: App, /) -> list[str]:
    """List the output modes one command actually offers.

    Parameters
    ----------
    command
        A leaf command's ``App``.

    Returns
    -------
    list[str]
        ``"human"`` always -- every command can talk to a person -- plus ``"json"`` and
        ``"dense"`` for each flag the command really declares. Read from the argument
        collection's ``__contains__``, which matches aliases, rather than from a table, so a
        command that forgot ``--dense`` is reported as lacking it instead of being advertised
        with a mode that would fail.
    """
    collection = command.assemble_argument_collection(parse_docstring=True)
    modes = [OutputMode.HUMAN.value]
    if "--json" in collection:
        modes.append(OutputMode.JSON.value)
    if "--dense" in collection:
        modes.append(OutputMode.DENSE.value)
    return modes


def _registrations(parent: App, /) -> tuple[tuple[str, tuple[str, ...], App], ...]:
    """Walk one ``App``'s children in registration order, folding aliases together.

    Parameters
    ----------
    parent
        The app whose children to list.

    Returns
    -------
    tuple[tuple[str, tuple[str, ...], App], ...]
        One entry per distinct child: its canonical CLI name, its other names, and the child.
        ``--help``, ``-h``, ``--version`` and ``--skill`` are dropped, since they are
        pseudo-commands registered into the same mapping. The first three are cyclopts';
        ``--skill`` is this CLI's, registered the same way and dropped for the same reason --
        this list means "operations a caller can perform", and printing a document is not one.
        :func:`rmspec.cli._skill.describe` is how the manifest publishes it instead.

    Notes
    -----
    **This is the only function in this package that reads a private cyclopts attribute**, and
    :data:`_COMMANDS_ATTRIBUTE` is the only place it is spelled. It is read for registration
    order, which is what ``--help`` prints and the only order a reader would recognise; each
    key is then resolved through the public ``App.__getitem__``.

    Aliases are folded by ``id()`` because ``App.command()`` stores the *same* object under
    every name it was given, so two keys pointing at one object are one command with two
    spellings and not two commands.
    """
    reserved = set(parent.help_flags) | set(parent.version_flags) | set(SKILL_FLAGS)
    canonical: dict[int, str] = {}
    order: list[str] = []
    children: dict[str, App] = {}
    aliases: dict[str, list[str]] = {}
    for key in getattr(parent, _COMMANDS_ATTRIBUTE):
        if key in reserved:
            continue
        child = parent[key]
        known = canonical.get(id(child))
        if known is not None:
            aliases[known].append(key)
            continue
        canonical[id(child)] = key
        order.append(key)
        children[key] = child
        aliases[key] = []
    return tuple((key, tuple(aliases[key]), children[key]) for key in order)


def _describe_command(
    command: App,
    path: tuple[str, ...],
    alternatives: tuple[str, ...],
    /,
) -> dict[str, Any]:
    """Describe one leaf command.

    Parameters
    ----------
    command
        The command's ``App``.
    path
        The invocation, as the words a user types after ``rmspec``.
    alternatives
        The command's other names, as bare keys.

    Returns
    -------
    dict[str, Any]
        The command's whole description. ``help`` is the docstring's first line, which is the
        summary numpydoc requires and the line ``--help`` shows in a command list.
    """
    invocation = " ".join(path)
    text = (command.help or "").strip()
    return {
        "name": invocation,
        "aliases": [" ".join((*path[:-1], alias)) for alias in alternatives],
        "help": text.splitlines()[0] if text else None,
        "response_types": response_types(invocation),
        "modes": _modes(command),
        "parameters": [
            _describe_parameter(argument)
            for argument in command.assemble_argument_collection(parse_docstring=True).filter_by(
                show=True
            )
        ],
    }


def _describe_commands(parent: App, path: tuple[str, ...], /) -> list[dict[str, Any]]:
    """Flatten the command tree into one list, depth first, in registration order.

    Parameters
    ----------
    parent
        The app to walk.
    path
        The invocation words that reach *parent*.

    Returns
    -------
    list[dict[str, Any]]
        One entry per **leaf**. A group -- an ``App`` with no ``default_command`` -- is not a
        command a caller can run, so it contributes its name to its children's invocations and
        nothing else. ``device`` is a group; ``device info`` is the command.
    """
    described: list[dict[str, Any]] = []
    for key, alternatives, child in _registrations(parent):
        here = (*path, key)
        if child.default_command is not None:
            described.append(_describe_command(child, here, alternatives))
        described.extend(_describe_commands(child, here))
    return described


def _error_classes() -> tuple[type[errors.RmspecError], ...]:
    """List the whole error tree, root first, each class exactly once.

    Returns
    -------
    tuple[type[~rmspec.domain.errors.RmspecError], ...]
        Every class in ``RmspecError``'s subtree, the root included. The root is in the list
        because its exit status is the one a caller gets for a class the sparse table never
        names, so leaving it out would hide the fallback.
    """
    found: list[type[errors.RmspecError]] = [errors.RmspecError]
    seen: set[type[errors.RmspecError]] = {errors.RmspecError}

    def descend(cls: type[errors.RmspecError], /) -> None:
        for child in cls.__subclasses__():
            if child in seen:
                continue
            seen.add(child)
            found.append(child)
            descend(child)

    descend(errors.RmspecError)
    return tuple(found)


def _describe_errors() -> list[dict[str, Any]]:
    """Describe every failure identity a caller may have to branch on.

    Returns
    -------
    list[dict[str, Any]]
        ``type`` is :attr:`~rmspec.domain.errors.RmspecError.code`, which is the class name
        and is what the failure envelope puts in ``error.type``. ``exit_code`` is the
        domain's own answer, obtained by scoring ``cls.__new__(cls)`` -- an instance carrying
        nothing but its ``__mro__``, so no field value is invented. ``abstract`` is true when
        the class has subclasses of its own, which is a structural fact rather than a reading
        of a docstring: an ``abstract`` entry is a name a caller will normally meet as one of
        its leaves, and the leaf's exit code may well have come from it.
    """
    return [
        {
            "type": cls.__name__,
            "exit_code": errors.exit_code(cls.__new__(cls)),
            "abstract": bool(cls.__subclasses__()),
        }
        for cls in _error_classes()
    ]


def _setting_docstrings() -> dict[str, str]:
    """Read each ``CliSettings`` field's own docstring out of the source.

    Returns
    -------
    dict[str, str]
        Field name to docstring. Empty for a field that has none.

    Notes
    -----
    ``CliSettings`` does not set ``use_attribute_docstrings``, so pydantic leaves every
    ``FieldInfo.description`` at ``None`` and the prose that documents each variable is
    reachable only from the source. Parsing it with ``ast`` is what makes the third of §3's
    promises true -- that ``env``, ``--help`` and the manifest cannot drift -- without editing
    a settings module this command has no business editing.
    """
    module = ast.parse(inspect.getsource(CliSettings))
    docstrings: dict[str, str] = {}
    field: str | None = None
    for node in ast.walk(module):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                field = statement.target.id
            elif (
                field is not None
                and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                docstrings[field] = inspect.cleandoc(statement.value.value)
                field = None
            else:
                field = None
    return docstrings


def _describe_settings() -> list[dict[str, Any]]:
    """Describe every ``RMSPEC_*`` variable, in declaration order.

    Returns
    -------
    list[dict[str, Any]]
        ``name`` is the variable a user exports, ``type`` the annotation, ``default`` the
        declared default, and ``help`` the first line of the field's own docstring.

    Notes
    -----
    Defaults come from ``CliSettings.model_construct()``, which resolves every
    ``default_factory`` **without reading the environment** -- the same trick
    ``describe_bindings`` uses, and for the same reason. Reporting the factory itself would
    put ``"<function default_ssh_key_path at 0x...>"`` in the document; reporting a value read
    from the environment would make the manifest describe this shell rather than this CLI.
    Two of the defaults are therefore machine-specific paths, which is the honest answer: they
    are what a run with no ``RMSPEC_*`` set would actually use.
    """
    defaults = CliSettings.model_construct()
    docstrings = _setting_docstrings()
    described: list[dict[str, Any]] = []
    for name, field in CliSettings.model_fields.items():
        text = docstrings.get(name, "")
        described.append(
            {
                "name": f"{SETTING_PREFIX}{name.upper()}",
                "field": name,
                "type": _type_name(field.annotation),
                "default": _jsonable(getattr(defaults, name)),
                "help": text.splitlines()[0] if text else None,
            }
        )
    return described


def _version(root: App, /) -> object:
    """Give the version the app declares, having called it when it is a callable.

    Parameters
    ----------
    root
        The application's root ``App``.

    Returns
    -------
    object
        The version string, or ``None`` when the app declares none.

    Notes
    -----
    ``App.version`` is ``None | str | Callable[..., str] | Callable[..., Coroutine[..., str]]``,
    and this CLI passes ``version=_version`` -- a callable. Anything that serialises it as it
    stands emits ``"<function _version at 0x...>"``, so this calls it. ``isinstance(..., str)``
    rather than ``callable(...)``: the latter narrows to a top callable whose signature is
    unknown, which is not safe to call and which the type checker rejects outright.
    """
    declared = root.version
    if declared is None or isinstance(declared, str):
        return declared
    return declared()


def build_manifest(root: App, /) -> dict[str, Any]:
    """Describe the whole CLI by introspection.

    Parameters
    ----------
    root
        The application's root ``App``.

    Returns
    -------
    dict[str, Any]
        The ``data`` payload of the ``manifest`` document: ``name``, ``version``, ``skill``, and
        the four sections ``commands``, ``errors``, ``degradation_kinds`` and ``settings``.

    Notes
    -----
    The version is resolved by :func:`_version`, which **calls** ``App.version`` when it is a
    callable -- which it is in this CLI. Serialising it as it stands would put
    ``"<function _version at 0x...>"`` in the document, the exact failure a test asserts
    cannot happen anywhere in the manifest.
    """
    return {
        "name": root.name[0],
        "version": _jsonable(_version(root)),
        # Second, and before the four sections, because it is what a first-contact reader should
        # act on: the pointer to the one document that says which of the commands below change
        # what a person sees. A pointer rather than the prose -- see `_skill.describe_skill`.
        "skill": describe_skill(),
        "commands": _describe_commands(root, ()),
        "errors": _describe_errors(),
        "degradation_kinds": [kind.value for kind in errors.DegradationKind],
        "settings": _describe_settings(),
    }


def manifest(*, json: JsonFlag = False, dense: DenseFlag = False) -> int:
    """Describe every command, error, degradation and setting this CLI has.

    Parameters
    ----------
    json
        Emit the manifest as one JSON envelope on stdout. The default already does, because a
        manifest has no human rendering worth having; the flag exists so that ``--json`` means
        the same thing on every command.
    dense
        Emit one tab-separated ``command  response_types  help`` record per command on stdout
        instead, the response types comma-separated. The whole manifest does not fit a record
        stream, so this projects the part an agent with a bounded context most often wants.
        Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0``. Introspection cannot fail against an app that imported, and ``2`` when both
        output flags were passed.

    Notes
    -----
    This command composes no container and reads no setting, so it answers on a machine with a
    mistyped ``RMSPEC_*`` and with no tablet attached -- which matters, because the manifest is
    where a caller goes to learn how those variables are spelled.
    """

    def body(out: CliOutput) -> int:
        data = build_manifest(importlib.import_module(_ENTRY_MODULE).app)
        if out.mode is OutputMode.DENSE:
            out.rows(
                ("command", "response_types", "help"),
                (
                    (
                        str(command["name"]),
                        ",".join(command["response_types"] or ()),
                        str(command["help"]),
                    )
                    for command in data["commands"]
                ),
            )
        else:
            out.emit(data, response_type=MANIFEST_RESPONSE_TYPE)
        return 0

    return render(body, json=json, dense=dense)
