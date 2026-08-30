"""The ``rmspec`` command line: the cyclopts app, and the two commands that prove the spine.

``[project.scripts]`` declares ``rmspec = "rmspec.cli:app"``, so importing this module is what
every invocation pays for and :data:`app` is the name it looks for.

Why this module imports no adapter
----------------------------------
Legacy loaded **447 modules** on ``rmspec --help`` -- including ``rmscene`` -- because its
command modules imported the formats package at module scope. Measured here: importing the six
adapter packages costs 821 modules and eagerly loads ``cairocffi``, ``rmscene``, ``httpx``,
``paramiko`` and ``boto3``. So this module imports :mod:`rmspec.app` and :mod:`rmspec.domain`,
which are pure, and reaches :mod:`rmspec.cli._container` -- the only module that names an
adapter -- from inside the one command that composes something.

That deferral is spelled ``import_module`` rather than a function-local ``import`` statement
because ruff's ``PLC0415`` forbids the latter and this repository allows no ``noqa``. The cost
is one ``Any`` at one call site, and it is immediately constrained: every value that crosses
the boundary is handed straight to a helper with a real annotation, so the rendering below is
type-checked even though the attribute lookup that produced it was not.

The opening every command shares
--------------------------------
Two lines, established here and copied by every command that follows::

    out, refusal = open_output(consoles, json=json, dense=dense)
    if refusal is not None:
        return out.fail(refusal)

:func:`~rmspec.cli._output.open_output` is what makes ``--json`` and ``--dense`` mutually
exclusive in one place, and returning the refusal rather than raising it means the failure
path is the same two lines a command already has instead of a ``try`` wrapped around the
construction of the object its ``except`` would need.

Both flags are annotated ``Parameter(negative="")``
---------------------------------------------------
Measured on cyclopts 4.6.0: an unannotated ``json: bool = False`` also registers ``--no-json``,
and ``rmspec doctor --no-json`` parsed cleanly to ``{'json': False}`` while being documented
nowhere -- not in ``--help``, and not in the manifest that will enumerate this surface. An
agent-facing CLI must not accept a flag its own manifest will not list, so every boolean
parameter here suppresses its auto-generated negative. There is no information loss: the
negative of a flag defaulting to ``False`` is the default.

A command returns ``int``, never ``bool``
----------------------------------------
cyclopts' default ``result_action="print_non_int_sys_exit"`` reaches
``if isinstance(result, bool): sys.exit(0 if result else 1)`` **before** it reaches its ``int``
branch (``cyclopts/_result_action.py:68-72``). So a command that returns ``True`` to mean
"failed" exits **0**, and one that returns ``False`` exits 1. Both commands below return the
domain's own status through :func:`~rmspec.domain.errors.exit_code`, and the ``-> int`` on each
signature carries a comment saying why it must not become ``bool``.

``rmspec --version`` reads whichever distribution actually shipped this CLI
--------------------------------------------------------------------------
Legacy read ``importlib.metadata.version("remarkable-spec")``, a distribution that does not
exist in this workspace, inside a ``try`` that swallowed ``PackageNotFoundError`` and fell back
to the string ``0.0.0-dev``. So ``--version`` was wrong and silent about being wrong.

The same code now ships from two distribution names, so :data:`DISTRIBUTIONS` is a tuple rather
than the single ``"rmspec-cli"`` it began as. In this workspace the nine members are installed
by name and ``rmspec-cli`` is the one that carries ``rmspec/cli/``; the artifact a user installs
is ``rmspec``, the single distribution ``mise run build`` stages out of all nine, and no
``rmspec-cli`` metadata exists beside it. Asking about one name only worked in the dev venv and
raised ``PackageNotFoundError`` out of the built wheel -- measured by installing it, which is
the only place that failure is reachable. :func:`_version` tries each name in order and still
swallows nothing when none is installed: a version that cannot be found is a packaging fault,
and the loudest possible failure is the cheapest one to fix.

Every other reader asks :func:`resolved_version`, because ``app.version`` holds the *callable*
and not the string. See that function for what serialising it naively emits.

The two commands
----------------
``rmspec doctor`` runs the eager dependency pass and renders
:class:`~rmspec.app.ReportCapabilities` out of a real container, which makes it the integration
test for the settings, the container and the output rules at once.

``rmspec env`` writes shell assignments through :meth:`~rmspec.cli._output.CliOutput.line`,
which touches no ``Console``. Legacy printed them through ``rich``, which off a tty wraps at 80
columns and parses ``[...]`` as markup, so ``eval "$(rmspec env)"`` broke on any path over about
65 characters. Every value also goes through ``shlex.quote``, so a path containing a space is
still one word after ``eval``.
"""

from __future__ import annotations

import os
import shlex
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import App, Parameter
from rich.table import Table

from rmspec.app import ReportCapabilities
from rmspec.cli._annotations import read_annotations
from rmspec.cli._device import info as device_info
from rmspec.cli._diagram import diagram
from rmspec.cli._ls import ls
from rmspec.cli._manifest import manifest
from rmspec.cli._ocr import ocr
from rmspec.cli._output import NextAction, OutputMode, make_console_pair, open_output
from rmspec.cli._push import push
from rmspec.cli._read import read
from rmspec.cli._render import render
from rmspec.cli._reply import reply
from rmspec.cli._search import search
from rmspec.cli._settings import CliSettings, apply_native_library_path, load_settings
from rmspec.cli._sync import sync
from rmspec.domain.errors import RmspecError, exit_code

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from rmspec.app import ReportCapabilitiesResult
    from rmspec.cli._container import DependencyFailure
    from rmspec.cli._output import CliOutput, ConsolePair

__all__ = [
    "CAPABILITIES_RESPONSE_TYPE",
    "DISTRIBUTIONS",
    "SETTINGS_RESPONSE_TYPE",
    "app",
    "device",
    "device_info",
    "diagram",
    "doctor",
    "env",
    "ls",
    "manifest",
    "ocr",
    "push",
    "read",
    "read_annotations",
    "render",
    "reply",
    "resolved_version",
    "search",
    "sync",
]

DISTRIBUTIONS = ("rmspec", "rmspec-cli")
"""Every distribution name that can ship this package, in the order ``--version`` asks.

``rmspec`` first because it is the artifact users install: one distribution carrying all nine
members' subpackages, staged by :mod:`rmspec.cli._bundle`. ``rmspec-cli`` second because that
is the member name, and inside this workspace it is the only one installed. Both are correct
answers to "what version is this" -- which one exists depends on how the CLI got here, and only
one of them ever exists at a time, so the order matters only if someone installs the bundle
into the dev venv, where both carry the version the members agree on anyway.

``remarkable-spec`` is the legacy single-distribution name and does not exist in this
workspace; asking about it is what produced ``0.0.0-dev``.
"""

CAPABILITIES_RESPONSE_TYPE = "capabilities"
"""``doctor``'s discriminator, from the frozen table in the step-7 design's §1.4.

Named for the payload rather than for the verb, because two commands may one day return the
same shape. A reader branches on this before it touches ``data``, and ``rmspec manifest`` will
publish it so an agent knows the shape *before* it calls.
"""

SETTINGS_RESPONSE_TYPE = "settings"
"""``env``'s discriminator, from the same frozen table."""

_CONTAINER_MODULE = "rmspec.cli._container"
"""The module deferred until a command actually composes something. See the module docstring."""

_CAPABILITY_COLUMNS = ("port", "state", "detail")
"""``doctor --dense``'s columns: the port's identity, whether it works, and what it refuses.

The same three facts the human table carries, without the box drawing -- which is the point of
``DENSE``. Not every field of the report: ``restricted`` and ``unavailable`` rows also carry
``operation`` and ``supported_by``, and projecting those would turn a grep-able summary back
into the document ``--json`` already serves properly.
"""

_SETTINGS_COLUMNS = ("variable", "value")
"""``env --dense``'s columns, over the settings that have a value.

Deliberately **not** the same as ``env``'s default output. That default is ``export VAR=value``
lines built for ``eval "$(rmspec env)"``, which is already machine-consumable and must not
change; ``--dense`` is for a reader that wants the pairs without parsing shell quoting. An
unset setting is omitted from both, for the same reason: ``RMSPEC_XOCHITL=''`` would claim a
mirror at the current directory.
"""


def _installed_version(distribution: str) -> str | None:
    """Report a distribution's version, or ``None`` when it is not installed.

    Parameters
    ----------
    distribution
        A distribution name.

    Returns
    -------
    str or None
        The version, or ``None``. The ``try`` lives here rather than in :func:`_version`'s loop
        so that asking about two names does not put a handler inside a loop body.
    """
    try:
        found = version(distribution)
    except PackageNotFoundError:
        return None
    return found


def _version() -> str:
    """Report this CLI's version from installed metadata.

    Returns
    -------
    str
        The version of the first entry of :data:`DISTRIBUTIONS` that is installed.

    Raises
    ------
    importlib.metadata.PackageNotFoundError
        None of them is installed. A single name being absent is expected -- two names are
        legitimate and only one is ever present -- but *all* of them being absent is a
        packaging fault. That failure is deliberately not swallowed: the legacy code caught it
        and printed ``0.0.0-dev``, which turned a broken install into a plausible-looking
        answer.
    """
    for distribution in DISTRIBUTIONS:
        found = _installed_version(distribution)
        if found is not None:
            return found
    names = " or ".join(DISTRIBUTIONS)
    raise PackageNotFoundError(names)


app = App(
    name="rmspec",
    version=_version,
    help="Read, render and sync reMarkable Paper Pro documents.",
)
"""The cyclopts application, and the object ``[project.scripts]`` resolves."""


def resolved_version() -> str:
    """Report the version string ``rmspec --version`` prints, as a string.

    Returns
    -------
    str
        The version of whichever entry of :data:`DISTRIBUTIONS` is installed.

    Notes
    -----
    :data:`app` is built with ``version=_version``, and cyclopts accepts
    ``None | str | Callable[..., str]`` there and calls it lazily. So ``app.version`` is a
    **function object**, and anything that serialises it naively emits
    ``"<function _version at 0x104ba1580>"`` where ``"0.2.0"`` was meant -- measured, not
    hypothesised. ``rmspec --version`` itself is correct because cyclopts calls the callable;
    the trap is for the next reader, and ``rmspec manifest``'s ``version`` field is exactly
    that reader.

    So every reader other than cyclopts asks here, and a test pins ``app.version is _version``
    so this function and the app cannot drift apart.
    """
    return _version()


def _streams() -> ConsolePair:
    """Build the process's two consoles over the real standard streams.

    Returns
    -------
    ConsolePair
        stdout for the machine, stderr for the human, and stdout again unadorned.
    """
    return make_console_pair(stdout=sys.stdout, stderr=sys.stderr)


def _shell_value(value: object, /) -> str | None:
    """Render one setting as the string the variable that reproduces it must hold.

    Parameters
    ----------
    value
        Whatever the field holds -- a path, a number, or a set of enum members.

    Returns
    -------
    str | None
        ``None`` for an unset setting, which is omitted rather than exported empty.
        Otherwise a string the settings loader accepts back.

    Notes
    -----
    A set field is comma-separated and **sorted**, and both halves are load-bearing.
    Measured: ``str(frozenset({OcrEngineName.TEXTRACT}))`` is
    ``"frozenset({<OcrEngineName.TEXTRACT: 'textract'>})"``, and
    ``eval "$(rmspec env)"`` followed by ``rmspec env`` therefore exited 78 with *its own*
    exported value rejected as unparseable. The sort is what makes the output the same on two
    runs, because a ``frozenset``'s iteration order depends on the hash seed and an
    ``eval``-able line that changes between runs is not reproducible.
    """
    if value is None:
        return None
    if isinstance(value, frozenset | set):
        return ",".join(sorted(str(member) for member in value))
    return str(value)


def _shell_variables(settings: CliSettings, /) -> dict[str, str | None]:
    """Project the settings onto the environment variables that would reproduce them.

    Parameters
    ----------
    settings
        The resolved settings.

    Returns
    -------
    dict[str, str | None]
        Variable name to value, in declaration order. ``None`` for a setting that is unset,
        which is a different statement from the empty string and is why the values are
        optional.
    """
    return {
        f"RMSPEC_{name.upper()}": _shell_value(getattr(settings, name))
        for name in CliSettings.model_fields
    }


def _capability_rows(report: ReportCapabilitiesResult, /) -> Iterator[tuple[str, str, str]]:
    """Project a capability report onto one record per port.

    Parameters
    ----------
    report
        What the composition root bound, partitioned by
        :class:`~rmspec.app.ReportCapabilities`.

    Yields
    ------
    tuple[str, str, str]
        Port, state, and the refusal that explains the state -- empty for a served port,
        because there is nothing it will not do.

    Notes
    -----
    Both renderings go through here, so ``doctor --dense`` and the human table cannot come to
    disagree about which ports exist or what state one is in. A generator rather than a list
    because :meth:`~rmspec.cli._output.CliOutput.rows` consumes its records once.
    """
    for port in report.served:
        yield (port, "served", "")
    for row in report.restricted:
        yield (row.port, "limited", row.refusal)
    for row in report.unavailable:
        yield (row.port, "unbound", row.refusal)


def _capability_table(report: ReportCapabilitiesResult, /) -> Table:
    """Render a capability report for a human.

    Parameters
    ----------
    report
        What the composition root bound.

    Returns
    -------
    Table
        One row per port, saying whether it works and what it will refuse -- the same records
        :func:`_capability_rows` yields, with borders.
    """
    table = Table(title=f"transport: {report.transport.value}")
    for column in _CAPABILITY_COLUMNS:
        table.add_column(column)
    for row in _capability_rows(report):
        table.add_row(*row)
    return table


def _capability_payload(
    report: ReportCapabilitiesResult,
    /,
    *,
    failures: Sequence[DependencyFailure],
) -> Mapping[str, Any]:
    """Build what goes under the envelope's ``data`` key for ``doctor``.

    Parameters
    ----------
    report
        What the composition root bound.
    failures
        Every unusable module the eager pass found.

    Returns
    -------
    Mapping[str, Any]
        The report plus ``missing``, JSON-ready. ``degradations`` is deliberately absent:
        the envelope hoists it to the top level so an agent looks in one place across all
        twelve use cases rather than learning twelve.
    """
    return {
        "transport": report.transport.value,
        "served": list(report.served),
        "restricted": [row.model_dump(mode="json") for row in report.restricted],
        "unavailable": [row.model_dump(mode="json") for row in report.unavailable],
        "missing": [failure.model_dump(mode="json") for failure in failures],
    }


def _render_diagnosis(
    out: CliOutput,
    /,
    *,
    failures: Sequence[DependencyFailure],
    report: ReportCapabilitiesResult,
) -> int:
    """Render the whole diagnosis once, and score it from the domain's table.

    Parameters
    ----------
    out
        Where to write. This is the helper that gives the deferred import's ``Any`` a real
        type again -- ``failures`` is annotated, so everything read off it below is checked.
    failures
        Every unusable module the eager pass found, not just the first.
    report
        The capability report.

    Returns
    -------
    int
        ``0`` when nothing is missing, otherwise
        :func:`~rmspec.domain.errors.exit_code` for the first failure's error.

    Notes
    -----
    Exactly one document reaches stdout. Emitting the report and then also emitting an error
    envelope would put two JSON values in the stream and make it unparseable, so the failures
    travel *inside* the report's payload and the exit status comes straight from the domain's
    table rather than from a second render.

    One branch, three arms, and no arm re-checks the mode. ``JSON`` carries the degradations
    inside the envelope; the other two report them on stderr, which is the only place they can
    go without breaking a homogeneous record stream. None of the three swallows one.
    """
    if out.mode is OutputMode.JSON:
        out.emit(
            _capability_payload(report, failures=failures),
            response_type=CAPABILITIES_RESPONSE_TYPE,
            degradations=report.degradations,
            next_action=_remedy(failures),
        )
    else:
        out.report_degradations(report.degradations)
        if out.mode is OutputMode.DENSE:
            out.rows(_CAPABILITY_COLUMNS, _capability_rows(report))
        else:
            out.display(_capability_table(report))
    for failure in failures:
        detail = "" if failure.detail is None else f" -- {failure.detail}"
        out.warn(f"{failure.feature} needs {failure.package} (extra: {failure.extra}){detail}")
    if not failures:
        return 0
    return exit_code(failures[0].as_error())


def _remedy(failures: Sequence[DependencyFailure], /) -> NextAction | None:
    """Give the literal command that fixes the first missing extra.

    Parameters
    ----------
    failures
        Every unusable module.

    Returns
    -------
    NextAction | None
        ``None`` when nothing is missing. Otherwise the ``uv sync`` line for every distinct
        extra at once, because a user missing two extras should run one command rather than
        learn about the second one tomorrow.
    """
    if not failures:
        return None
    extras = sorted({failure.extra for failure in failures})
    flags = " ".join(f"--extra {extra}" for extra in extras)
    return NextAction(
        command=f"uv sync {flags}",
        purpose=f"install the {len(extras)} missing extra(s) this binding needs",
    )


@app.command(name="doctor")
# The return type is int and must stay int. cyclopts' default
# result_action="print_non_int_sys_exit" tests isinstance(result, bool) *before* int, so a
# command that returned True to mean "failed" would exit 0.
def doctor(
    *,
    json: Annotated[bool, Parameter(name="--json", negative="")] = False,
    dense: Annotated[bool, Parameter(name="--dense", negative="")] = False,
) -> int:
    """Report what this binding can do, and what it is missing.

    Parameters
    ----------
    json
        Emit the report as one JSON envelope on stdout instead of a table on stderr.
    dense
        Emit one tab-separated ``port state detail`` record per port on stdout instead --
        the table's information without the box drawing. Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0`` when every selected dependency is usable, otherwise the domain's exit status
        for the first failure -- ``EX_CONFIG``, 78, for a missing extra. ``2`` when both
        output flags were passed.

    Notes
    -----
    This is the command that composes a real container, so it is also where a broken setting,
    a broken binding or a broken stream shows up first.
    """
    consoles = _streams()
    out, refusal = open_output(consoles, json=json, dense=dense)
    if refusal is not None:
        return out.fail(refusal)
    try:
        apply_native_library_path(os.environ)
        settings = load_settings()
        module: Any = import_module(_CONTAINER_MODULE)
        failures = module.probe_features(module.ImportProbe(), tuple(module.Feature))
        container = module.compose(settings=settings, consoles=consoles)
        try:
            report = container.get(ReportCapabilities).report(module.describe_bindings())
        finally:
            container.close()
    except RmspecError as err:
        return out.fail(err)
    return _render_diagnosis(out, failures=failures, report=report)


@app.command(name="env")
# int, not bool -- see the comment on doctor and the module docstring.
def env(
    *,
    json: Annotated[bool, Parameter(name="--json", negative="")] = False,
    dense: Annotated[bool, Parameter(name="--dense", negative="")] = False,
) -> int:
    """Print the resolved settings as shell assignments you can ``eval``.

    Parameters
    ----------
    json
        Emit the settings as one JSON envelope on stdout instead of shell assignments.
    dense
        Emit one tab-separated ``variable value`` record per set setting on stdout instead.
        Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0``, or the domain's exit status when a ``RMSPEC_*`` variable is unusable, or ``2``
        when both output flags were passed.

    Notes
    -----
    ``eval "$(rmspec env)"`` is the point, so the default assignments go through a plain
    writer and every value through ``shlex.quote``. An unset setting is omitted rather than
    exported as the empty string, because exporting ``RMSPEC_XOCHITL=''`` would claim a mirror
    at the current directory.

    ``--dense`` does not replace that default with a tab-separated version of itself: the
    default output is already machine-consumable and is the only form ``eval`` accepts, so it
    is frozen. ``--dense`` serves the different reader that wants ``variable<TAB>value``
    without unpicking shell quoting, and ``--json`` serves the one that wants an unset setting
    stated as ``null`` rather than omitted.
    """
    out, refusal = open_output(_streams(), json=json, dense=dense)
    if refusal is not None:
        return out.fail(refusal)
    try:
        settings = load_settings()
    except RmspecError as err:
        return out.fail(err)
    variables = _shell_variables(settings)
    if out.mode is OutputMode.JSON:
        out.emit(
            variables,
            response_type=SETTINGS_RESPONSE_TYPE,
            next_action=NextAction(
                command='eval "$(rmspec env)"',
                purpose="export these settings into the current shell",
            ),
        )
    elif out.mode is OutputMode.DENSE:
        out.rows(
            _SETTINGS_COLUMNS,
            ((name, value) for name, value in variables.items() if value is not None),
        )
    else:
        for name, value in variables.items():
            if value is not None:
                out.line(f"export {name}={shlex.quote(value)}")
    return 0


device = App(name="device", help="Inspect the attached tablet.")
"""The one sub-app in an otherwise flat verb namespace.

A flat namespace is what an agent-facing surface wants, and twelve of the thirteen verbs are
flat. ``device`` is a group rather than a verb because ``info`` is a noun about the device and a
sibling verb would have to be called something like ``device-info``, which reads as a word
nobody would type. It carries no ``default_command``: it contributes its word and nothing else,
which is what :mod:`rmspec.cli._manifest` expects when it walks the command tree.
"""

device.command(device_info)
app.command(device)

app.command(ls)
app.command(read)
app.command(render)
app.command(ocr)
app.command(diagram)
app.command(read_annotations)
app.command(search)
app.command(sync)
app.command(push)
app.command(reply)
app.command(manifest)
