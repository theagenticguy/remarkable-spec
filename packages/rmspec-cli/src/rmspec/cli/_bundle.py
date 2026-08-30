"""Stage one distribution from the nine workspace members, so a single wheel carries the CLI.

The nine distributions are a *development-time* boundary. ``tests/architecture`` enforces the
import direction between them, and each is a real package with its own manifest -- which is
exactly what makes ``rmspec-cli``'s own wheel uninstallable anywhere but this workspace. Its
metadata requires eight ``rmspec-*`` distributions that exist on no index, so
``uv tool install rmspec-cli`` fails on the first of them. The artifact a user installs
therefore cannot be one of the nine; it has to be a tenth thing that carries all of their code
and none of their internal requirements.

That is the whole job of this module. It writes a build tree whose ``src/rmspec/`` holds every
member's subpackage, and whose ``pyproject.toml`` declares the *union* of their third-party
requirements with the ``rmspec-*`` ones dropped -- those are no longer requirements, they are
contents. ``mise run build`` stages the tree and then runs ``uv build`` over it, and the result
is one wheel plus one sdist that install the whole CLI, ``rmspec`` entry point included.

Why a staged tree rather than a tenth package under ``packages/``
-----------------------------------------------------------------
Every file in ``tests/architecture`` enumerates ``packages/*`` and asserts a property of each
member: a ``--cov`` flag in two mise tasks, a ``src/rmspec/<name>`` module that imports, an
``__all__``, a ``py.typed`` marker, declared-equals-imported dependencies. A distribution whose
entire content is copied from its siblings satisfies none of those honestly, and what it could
satisfy it would satisfy by duplicating. It would also have to appear in
``[tool.uv.workspace] members``, which installs it into the dev venv beside the nine editables
it copies -- two providers of ``rmspec.cli`` on one ``sys.path``.

Why nothing is committed
------------------------
The staged tree lands in ``build/`` and is gitignored. A committed bundle manifest would be a
second place the dependency set is written down, and the second place is the one that goes
stale -- the failure ``AGENTS.md`` needs a ``--check`` mode to prevent. Here there is nothing
to check because there is nothing to drift: the manifest is derived from the nine at build
time, or the build does not happen.

Disagreements are errors, not choices
-------------------------------------
One wheel carries one specifier per requirement. When two members ask for the same
distribution with different specifiers there is no correct union, so this refuses rather than
picking -- a silently chosen bound ships a requirement no member asked for. The same rule
applies to the version: the nine share one, and the bundle has no basis for inventing another.

The namespace survives the copy
-------------------------------
The staged manifest sets ``module-name = "rmspec"`` with ``namespace = true``, and no
``rmspec/__init__.py`` is ever written, so the wheel ships nine sibling subpackages under one
PEP 420 namespace exactly as the nine separate wheels do. Verified against ``uv_build`` 0.11
rather than assumed: a single-segment namespace module name is accepted, and the built wheel
contains ``rmspec/app/``, ``rmspec/cli/`` and the rest with no ``rmspec/__init__.py`` beside
them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "BUNDLE_DESCRIPTION",
    "BUNDLE_NAME",
    "COPIED_FILES",
    "ENTRY_POINT_MEMBER",
    "NAMESPACE",
    "STAGE_TASK",
    "BundleError",
    "Member",
    "main",
    "read_members",
    "render_pyproject",
    "stage",
]

BUNDLE_NAME: Final = "rmspec"
"""Distribution name of the single artifact.

It matches the import namespace and the console script rather than any member, because it is
the only thing a user names: ``uv tool install rmspec``, then ``rmspec --help``.
"""

BUNDLE_DESCRIPTION: Final = (
    "reMarkable Paper Pro toolkit: v6 .rm parsing, rendering, OCR and device access, "
    "with the rmspec CLI"
)
"""The bundle's own ``description``.

Not derived: no member describes the whole, and concatenating nine descriptions would produce
a sentence none of them wrote.
"""

NAMESPACE: Final = "rmspec"
"""The PEP 420 namespace every member contributes a subpackage to."""

WORKSPACE_PREFIX: Final = "rmspec-"
"""Requirements starting with this are workspace members, so the bundle contains them.

A third-party distribution on PyPI whose name began with ``rmspec-`` would be dropped by this
filter. None exists, and the alternative -- matching against the member list -- reads the same
but fails silently when a member is renamed rather than loudly when one is added.
"""

ENTRY_POINT_MEMBER: Final = "rmspec-cli"
"""The member whose metadata the bundle inherits.

``authors``, ``license``, ``requires-python``, ``[project.scripts]`` and ``[build-system]`` are
copied from this one manifest rather than retyped here, so the bundle cannot claim a different
Python floor or a different entry point from the package that defines the command.
"""

COPIED_FILES: Final = ("README.md", "LICENSE")
"""Workspace-root files the staged tree needs.

``readme`` is declared metadata, so a missing ``README.md`` fails the build; ``LICENSE`` is not,
which is precisely why it is easy to ship a wheel without one.
"""

STAGE_TASK: Final = "mise run build"
"""The task that regenerates the staged tree, named in the generated manifest's own header."""


class BundleError(Exception):
    """The nine members cannot be reduced to one distribution as they stand."""


@dataclass(frozen=True, slots=True)
class Member:
    """One workspace member, as its own ``pyproject.toml`` describes it.

    Attributes
    ----------
    distribution
        The ``[project] name``, e.g. ``rmspec-app``.
    version
        The ``[project] version``. All members must agree.
    module
        The dotted module the member ships, from ``[tool.uv.build-backend] module-name``,
        e.g. ``rmspec.app``. Read rather than derived from *distribution* so that the staged
        tree places code where the member says it lives.
    source
        Absolute path to the directory *module* names.
    requirements
        Third-party requirement specifiers only; ``rmspec-*`` entries are dropped.
    extras
        Optional-dependency groups, each already filtered the same way.
    metadata
        The whole parsed manifest, for the fields the bundle inherits rather than merges.
    """

    distribution: str
    version: str
    module: str
    source: Path
    requirements: tuple[str, ...]
    extras: Mapping[str, tuple[str, ...]]
    metadata: Mapping[str, Any]


def _load(pyproject: Path) -> dict[str, Any]:
    """Parse a manifest.

    Parameters
    ----------
    pyproject
        Path to a ``pyproject.toml``.

    Returns
    -------
    dict
        The parsed document.
    """
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))


def requirement_name(spec: str) -> str:
    """Reduce a requirement specifier to its normalised distribution name.

    Parameters
    ----------
    spec
        A PEP 508 specifier, e.g. ``pyobjc-framework-vision>=12.1; sys_platform == 'darwin'``.

    Returns
    -------
    str
        The lowercased, dash-normalised name, e.g. ``pyobjc-framework-vision``.
    """
    head = spec.split(";", maxsplit=1)[0].strip()
    name = head.split("[")[0]
    for operator in ("===", ">=", "<=", "==", "!=", "~=", ">", "<"):
        name = name.split(operator)[0]
    return name.strip().lower().replace("_", "-")


def _third_party(specs: Iterable[str]) -> tuple[str, ...]:
    """Drop the workspace's own distributions from a requirement list.

    Parameters
    ----------
    specs
        Requirement specifiers as written in a member's manifest.

    Returns
    -------
    tuple of str
        Those that name something outside this workspace.
    """
    return tuple(spec for spec in specs if not requirement_name(spec).startswith(WORKSPACE_PREFIX))


def read_members(workspace: Path) -> tuple[Member, ...]:
    """Read every member manifest under ``<workspace>/packages/``.

    Parameters
    ----------
    workspace
        The workspace root -- the directory holding ``packages/``. Passed in rather than
        guessed, because this module ships inside an installed wheel where no repository root
        exists; ``mise.toml`` supplies it, being the one file here that spells commands out.

    Returns
    -------
    tuple of Member
        One per member, ordered by distribution name.

    Raises
    ------
    BundleError
        When no member was found, when a member ships a module outside the ``rmspec``
        namespace, or when the directory its ``module-name`` names is absent.
    """
    members: list[Member] = []
    for pyproject in sorted((workspace / "packages").glob("*/pyproject.toml")):
        document = _load(pyproject)
        project = document["project"]
        module = str(document["tool"]["uv"]["build-backend"]["module-name"])
        if not module.startswith(f"{NAMESPACE}."):
            msg = (
                f"{pyproject} ships module {module!r}, which is outside the {NAMESPACE!r} "
                f"namespace. The bundle stages every member under one namespace package and "
                f"has nowhere to put this one."
            )
            raise BundleError(msg)
        source = pyproject.parent.joinpath("src", *module.split("."))
        if not source.is_dir():
            msg = (
                f"{pyproject} names module {module!r}, but {source} does not exist. The "
                f"bundle would ship a distribution missing that subpackage."
            )
            raise BundleError(msg)
        members.append(
            Member(
                distribution=str(project["name"]),
                version=str(project["version"]),
                module=module,
                source=source,
                requirements=_third_party(project.get("dependencies", [])),
                extras={
                    name: _third_party(specs)
                    for name, specs in (project.get("optional-dependencies") or {}).items()
                },
                metadata=document,
            )
        )
    if not members:
        msg = (
            f"no member manifests under {workspace / 'packages'}. Either the workspace root "
            f"argument is wrong or there is nothing to bundle."
        )
        raise BundleError(msg)
    return tuple(members)


def _merge(claims: Iterable[tuple[str, str]]) -> tuple[str, ...]:
    """Union requirement specifiers, refusing to choose when two members disagree.

    Parameters
    ----------
    claims
        ``(distribution that asked, specifier)`` pairs, across every member.

    Returns
    -------
    tuple of str
        One specifier per requirement, ordered by requirement name.

    Raises
    ------
    BundleError
        When one requirement is asked for with two different specifiers.
    """
    chosen: dict[str, tuple[str, str]] = {}
    for asker, spec in claims:
        name = requirement_name(spec)
        previous = chosen.get(name)
        if previous is not None and previous[1] != spec:
            msg = (
                f"{name} is required as {previous[1]!r} by {previous[0]} and as {spec!r} by "
                f"{asker}. One wheel carries one specifier per requirement, and picking "
                f"between them here would ship a bound no member asked for. Make the two "
                f"manifests agree."
            )
            raise BundleError(msg)
        chosen.setdefault(name, (asker, spec))
    return tuple(chosen[name][1] for name in sorted(chosen))


def _one_version(members: Sequence[Member]) -> str:
    """Return the version the members share.

    Parameters
    ----------
    members
        Every member.

    Returns
    -------
    str
        The single version found.

    Raises
    ------
    BundleError
        When they do not all agree, since the bundle has no basis for inventing a version.
    """
    versions = {member.version for member in members}
    if len(versions) != 1:
        found = ", ".join(f"{m.distribution} {m.version}" for m in members)
        msg = f"the members disagree about the version, so the bundle has none to claim: {found}"
        raise BundleError(msg)
    return versions.pop()


def _entry_point(members: Sequence[Member]) -> Member:
    """Return the member whose metadata the bundle inherits.

    Parameters
    ----------
    members
        Every member.

    Returns
    -------
    Member
        The one named by :data:`ENTRY_POINT_MEMBER`.

    Raises
    ------
    BundleError
        When it is absent, since the bundle would then have no console script and no basis for
        its Python floor.
    """
    for member in members:
        if member.distribution == ENTRY_POINT_MEMBER:
            return member
    found = ", ".join(member.distribution for member in members)
    msg = (
        f"{ENTRY_POINT_MEMBER} is not among the members, so there is no entry point to ship "
        f"and no manifest to inherit authors, license and requires-python from. Found: {found}"
    )
    raise BundleError(msg)


def _quote(value: str) -> str:
    r"""Render a string as a TOML basic string.

    Parameters
    ----------
    value
        The string to quote.

    Returns
    -------
    str
        A quoted, escaped TOML basic string. ``json.dumps`` is the right tool rather than a
        near-miss one: JSON's string escapes are a subset of TOML's basic-string escapes, and
        its default ``ensure_ascii`` produces ``\uXXXX``, which TOML also accepts.
    """
    return json.dumps(value)


def _inline_table(mapping: Mapping[str, str]) -> str:
    """Render a flat mapping as a TOML inline table.

    Parameters
    ----------
    mapping
        Keys and string values, e.g. an entry of ``[project] authors``.

    Returns
    -------
    str
        ``{ key = "value", ... }``, keys in their original order.
    """
    body = ", ".join(f"{key} = {_quote(str(value))}" for key, value in mapping.items())
    return f"{{ {body} }}"


def render_pyproject(members: Sequence[Member]) -> str:
    """Render the manifest for the single distribution.

    Parameters
    ----------
    members
        Every member, from :func:`read_members`.

    Returns
    -------
    str
        A complete ``pyproject.toml``.

    Raises
    ------
    BundleError
        When the members cannot be reduced to one manifest -- see :func:`_merge`,
        :func:`_one_version` and :func:`_entry_point`.
    """
    entry = _entry_point(members)
    project = entry.metadata["project"]
    build_system = entry.metadata["build-system"]
    requirements = _merge(
        (member.distribution, spec) for member in members for spec in member.requirements
    )
    extras = {
        name: _merge(
            (member.distribution, spec)
            for member in members
            for spec in member.extras.get(name, ())
        )
        for name in sorted({name for member in members for name in member.extras})
    }
    lines = [
        f"#  Generated by `{STAGE_TASK}` from the {len(members)} members of the",
        "#  remarkable-spec workspace. Not committed, not edited by hand: it is derived from",
        "#  their manifests on every build, so it cannot drift from them.",
        "",
        "[project]",
        f"name = {_quote(BUNDLE_NAME)}",
        f"version = {_quote(_one_version(members))}",
        f"description = {_quote(BUNDLE_DESCRIPTION)}",
        f"readme = {_quote(COPIED_FILES[0])}",
        f"license = {_quote(str(project['license']))}",
        f"requires-python = {_quote(str(project['requires-python']))}",
        "authors = [",
        *(f"    {_inline_table(author)}," for author in project["authors"]),
        "]",
        "dependencies = [",
        *(f"    {_quote(spec)}," for spec in requirements),
        "]",
        "",
        "[project.optional-dependencies]",
        *(
            line
            for name, specs in extras.items()
            for line in (f"{name} = [", *(f"    {_quote(spec)}," for spec in specs), "]")
        ),
        "",
        "[project.scripts]",
        *(
            f"{name} = {_quote(str(target))}"
            for name, target in sorted(project["scripts"].items())
        ),
        "",
        "[build-system]",
        "requires = [",
        *(f"    {_quote(str(spec))}," for spec in build_system["requires"]),
        "]",
        f"build-backend = {_quote(str(build_system['build-backend']))}",
        "",
        "#  One distribution, nine subpackages, still a PEP 420 namespace: no",
        "#  src/rmspec/__init__.py is staged, so the subpackages stay siblings.",
        "[tool.uv.build-backend]",
        f"module-name = {_quote(NAMESPACE)}",
        "namespace = true",
        "",
    ]
    return "\n".join(lines)


def stage(workspace: Path, destination: Path) -> tuple[Member, ...]:
    """Write the single-distribution build tree, replacing whatever was there.

    Parameters
    ----------
    workspace
        The workspace root holding ``packages/``.
    destination
        Directory to stage into. Removed first if it exists: a stale copy of a module that has
        since been deleted would otherwise be shipped, and the whole point of building from
        this tree is that it is exactly what the nine members currently say.

    Returns
    -------
    tuple of Member
        The members that were staged.

    Raises
    ------
    BundleError
        When the members cannot be reduced to one distribution.
    """
    members = read_members(workspace)
    document = render_pyproject(members)
    if destination.exists():
        shutil.rmtree(destination)
    namespace_root = destination / "src" / NAMESPACE
    namespace_root.mkdir(parents=True)
    for member in members:
        target = namespace_root.joinpath(*member.module.split(".")[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            member.source,
            target,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    (destination / "pyproject.toml").write_text(document, encoding="utf-8")
    for name in COPIED_FILES:
        shutil.copy2(workspace / name, destination / name)
    return members


def main(argv: Sequence[str] | None = None) -> int:
    """Stage the build tree, or report why the members cannot be bundled.

    Parameters
    ----------
    argv
        Command-line words, or ``None`` to read :data:`sys.argv`.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the members cannot be reduced to one distribution. Both
        paths are arguments rather than constants for the reason given in
        :func:`read_members`.
    """
    parser = argparse.ArgumentParser(
        prog="rmspec-bundle",
        description=stage.__doc__,
    )
    parser.add_argument("workspace", type=Path, help="the workspace root, holding packages/")
    parser.add_argument("destination", type=Path, help="directory to stage the build tree into")
    arguments = parser.parse_args(argv)
    try:
        members = stage(arguments.workspace, arguments.destination)
    except BundleError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    names = ", ".join(member.module for member in members)
    sys.stderr.write(
        f"staged {BUNDLE_NAME} in {arguments.destination} from {len(members)} members: {names}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
