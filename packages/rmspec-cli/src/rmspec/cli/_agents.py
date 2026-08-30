"""Render ``AGENTS.md`` from ``rmspec manifest``, so a committed doc cannot contradict the code.

A hand-written agent-facing document is a second surface description, and the second one is
always the one that goes stale: the first time a flag is added, a caller that trusted the
Markdown branches on a shape the CLI no longer emits. So this module derives the whole document
from :func:`~rmspec.cli._manifest.build_manifest` -- the same introspection ``rmspec manifest``
publishes -- plus the envelope key constants in :mod:`rmspec.cli._output` and the fields of
:class:`~rmspec.domain.errors.Degradation`, :class:`~rmspec.cli._output.ErrorEnvelope` and
:class:`~rmspec.cli._output.NextAction`. Nothing about the surface is retyped here.

Two modes, and the second is the one that gives the first teeth
--------------------------------------------------------------
``--check`` regenerates into memory and compares against the committed file, printing a unified
diff and failing when they differ. Generation alone only makes the document *derivable*; the
check is what makes CI able to catch a surface change that shipped without regenerating, which
is the only failure mode a generated doc has.

What is *not* derived, and how it is kept honest
------------------------------------------------
Three things are prose in this file rather than facts in the manifest: the two calling
conventions (stdout versus stderr, and ``--pages`` being 0-based), the sentence explaining what
a degradation is, and :data:`EXIT_CODE_MEANINGS`. The first two are decisions rather than
surface, so no introspection could supply them. The third is a label per ``sysexits.h`` code,
and ``test_cli_agents.py`` asserts the label set and the manifest's code set are equal in both
directions -- so a new exit code with no label, or a label for a code nothing uses, fails the
build rather than shipping a table with a hole in it.

Why it lives in ``rmspec/cli/``
-------------------------------
It has to import ``build_manifest`` and the output-envelope constants; next to them that is a
plain first-party import with no path manipulation. Under ``packages/`` it also inherits every
gate that matters -- ruff at ``select = ["ALL"]``, ``ty --error-on-warning`` (whose ``[src]``
include is ``packages`` and ``tests``), the coverage floor and ``diff-cover`` -- and a root
``tools/`` directory is inside none of them, ty would not even look at it. The file whose whole
job is that ``AGENTS.md`` cannot drift should not be the least-checked file in the repository.
The cost is that a private module ships inside the ``rmspec-cli`` wheel; it is ``_``-prefixed,
``__init__.py`` never imports it, and no command registers it, so ``rmspec --help`` stays
adapter-free and fast.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from rmspec.cli import app
from rmspec.cli._manifest import _type_name, build_manifest
from rmspec.cli._output import (
    API_VERSION,
    API_VERSION_KEY,
    DATA_KEY,
    DEGRADATIONS_KEY,
    ERROR_KEY,
    ERROR_RESPONSE_TYPE,
    NEXT_ACTION_KEY,
    TYPE_KEY,
    ErrorEnvelope,
    NextAction,
    OutputMode,
)
from rmspec.domain.errors import Degradation

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "EXIT_CODE_MEANINGS",
    "GENERATED_BANNER",
    "OUTPUT_FLAGS",
    "REGENERATE_TASK",
    "main",
    "render_document",
]

REGENERATE_TASK: Final = "mise run agents-md"
"""The task that rewrites the document, named in its own banner.

A task rather than a command string, because ``mise.toml`` is the only file in this repository
that spells a command out -- ``lefthook.yml`` and CI both call tasks for the same reason.
"""

GENERATED_BANNER: Final = (
    f"<!-- Generated from `rmspec manifest`. Do not edit; run `{REGENERATE_TASK}`. -->"
)
"""The first line of the document, so a human who opens it to fix a typo is told not to."""

OUTPUT_FLAGS: Final = frozenset({"--json", "--dense"})
"""The two flags every command shares, described once in the modes section instead of 13 times.

A parameter whose only spelling is one of these is dropped from its command's table; the
command's ``modes`` list is printed instead, which carries the same fact in one cell and keeps
26 identical rows out of a document that is read into a context window.
"""

EXIT_CODE_MEANINGS: Final = {
    1: "unspecified failure -- the root error class, for a class the table never names",
    2: "the command line was wrong",
    65: "input data was malformed",
    66: "the named input does not exist",
    69: "a required service was unavailable",
    70: "an internal error",
    73: "an output file could not be created",
    74: "an I/O error",
    75: "a temporary failure -- retrying may work",
    77: "permission was denied",
    78: "the configuration is wrong",
}
"""One label per exit status the error tree can produce, following ``sysexits.h``.

The one table here the manifest cannot supply, because a status's *meaning* is not a fact about
the code. ``test_cli_agents.py`` pins this set equal to the manifest's, both ways.
"""

_TABLE_SEPARATOR: Final = "---"
"""One Markdown table rule cell."""


def _code(text: object, /) -> str:
    """Wrap one value in a Markdown code span, escaping the pipe a table would eat.

    Parameters
    ----------
    text
        Anything ``str`` can render: a type name, a JSON literal, a flag.

    Returns
    -------
    str
        The value in backticks, with ``|`` escaped.

    Notes
    -----
    A code span does **not** protect a pipe inside a table row -- ``Path | None`` in a cell
    silently becomes two cells and shifts every column after it. GFM's answer is to escape the
    pipe even inside the span, which is what this does; the reader sees ``Path | None``.
    """
    escaped = str(text).replace("|", r"\|")
    return f"`{escaped}`"


def _field_types(fields: Mapping[str, Any], /) -> dict[str, str]:
    """Describe one model's fields as a JSON object whose values are the field types.

    Parameters
    ----------
    fields
        A pydantic model's ``model_fields``.

    Returns
    -------
    dict[str, str]
        Field name to rendered annotation, in declaration order.

    Notes
    -----
    The envelope examples show types rather than sample data on purpose: sample data invites a
    caller to pattern-match on a value that was invented here, while a type is a claim the
    model itself makes. Rendered by :func:`~rmspec.cli._manifest._type_name`, the same function
    the manifest's own ``type`` fields go through, so the two documents name a type identically.
    """
    return {name: _type_name(field.annotation) for name, field in fields.items()}


def _inline(text: object, /) -> str:
    """Flatten one introspected help string into a single Markdown table cell.

    Parameters
    ----------
    text
        A ``help`` value from the manifest, possibly ``None`` and possibly containing newlines
        and reStructuredText double backticks.

    Returns
    -------
    str
        One line, empty when there was no help.

    Notes
    -----
    ``str.split()`` with no argument collapses every whitespace run, which is what keeps a
    wrapped numpydoc paragraph from breaking the table it lands in -- a raw newline inside a
    cell ends the row. ``|`` is escaped for the same reason. The double backticks become single
    ones because the source is numpydoc and the destination is Markdown.
    """
    if text is None:
        return ""
    return " ".join(str(text).replace("``", "`").split()).replace("|", r"\|")


def _literal(value: object, /) -> str:
    """Render one default or choice the way a caller would read it back out of JSON.

    Parameters
    ----------
    value
        Any value :func:`~rmspec.cli._manifest._jsonable` already reduced.

    Returns
    -------
    str
        The JSON spelling in a code span: ``` `null` ```, ``` `64` ```, ``` `"usb"` ```.

    Notes
    -----
    JSON rather than Python, because the document describes a JSON envelope and ``None`` versus
    ``null`` is the kind of small mismatch that costs a caller a retry.
    """
    return _code(json.dumps(value))


def _home_relative(value: object, /) -> object:
    """Rewrite a default that sits under the running user's home as a ``~`` path.

    Parameters
    ----------
    value
        A setting default from the manifest.

    Returns
    -------
    object
        The value, with a leading home directory replaced by ``~``.

    Notes
    -----
    Two defaults come from a ``default_factory`` rooted at :meth:`pathlib.Path.home`, so the
    manifest reports an absolute path naming whoever ran it. Committing that would make
    ``--check`` fail on every other machine, including CI -- the drift gate would report drift
    that is really just a different ``HOME``. ``~`` is both host-independent and the spelling a
    reader would write.
    """
    if not isinstance(value, str):
        return value
    home = Path.home()
    candidate = Path(value)
    if candidate == home or not candidate.is_relative_to(home):
        return value
    return f"~/{candidate.relative_to(home)}"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]], /) -> list[str]:
    """Build one Markdown table.

    Parameters
    ----------
    header
        Column names.
    rows
        One already-rendered cell sequence per row, each the same length as *header*.

    Returns
    -------
    list[str]
        The header line, the rule line, then one line per row.
    """
    lines = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(_TABLE_SEPARATOR for _ in header)} |",
    ]
    lines.extend(f"| {' | '.join(row)} |" for row in rows)
    return lines


def _fence(language: str, body: str, /) -> list[str]:
    """Wrap a block in a fenced code block.

    Parameters
    ----------
    language
        The info string. Never ``python``: ``ruff format`` reformats Python blocks inside
        Markdown and this file is neither excluded nor regenerated by the formatter, so a
        Python block would let ``format-check`` and ``--check`` disagree about the same bytes.
    body
        The block's contents, without a trailing newline.

    Returns
    -------
    list[str]
        The opening fence, the body's lines, and the closing fence.
    """
    return [f"```{language}", *body.splitlines(), "```"]


def _conventions() -> list[str]:
    """State the two calling conventions an agent gets wrong first.

    Returns
    -------
    list[str]
        The section's lines.
    """
    return [
        "## Two conventions, before anything else",
        "",
        "- **stdout is the machine's, stderr is the human's.** `--json` and `--dense` write to",
        "  stdout and nothing else does; the default human rendering, every table, every",
        "  degradation notice and every error line go to stderr. So `rmspec ls --json | jq` is",
        "  clean by construction and `rmspec ls 2>/dev/null` is correctly silent.",
        "- **`--pages` is 0-based**, matching `page_index` in every payload. A spec is a",
        "  comma-separated list of indices and inclusive `A-B` ranges: `0`, `2-5`, `0,3,7-9`.",
        "  A descending range, an empty spec, `--limit 0`, `--max-pages 0`, and `--pages`",
        "  together with `--limit` are all refused as `UsageError` before any work is paid for.",
        "  A human typing `--pages 1` gets the *second* page; that cost is deliberate, because",
        "  the primary caller has just read `page_index` out of a previous payload.",
    ]


def _modes_section() -> list[str]:
    """Describe the three output modes and the two envelope shapes.

    Returns
    -------
    list[str]
        The section's lines.

    Notes
    -----
    Every key and every field name here comes from the constants and models that produce the
    real documents, so the examples cannot describe an envelope the CLI does not write.
    """
    success = json.dumps(
        {
            API_VERSION_KEY: API_VERSION,
            TYPE_KEY: "<one of the command's response types>",
            DATA_KEY: {"...": "the use case's result, snake_case throughout"},
            DEGRADATIONS_KEY: [_field_types(Degradation.model_fields)],
            NEXT_ACTION_KEY: _field_types(NextAction.model_fields),
        },
        indent=2,
    )
    failure = json.dumps(
        {
            API_VERSION_KEY: API_VERSION,
            TYPE_KEY: ERROR_RESPONSE_TYPE,
            ERROR_KEY: _field_types(ErrorEnvelope.model_fields),
            NEXT_ACTION_KEY: _field_types(NextAction.model_fields),
        },
        indent=2,
    )
    return [
        "## Output modes",
        "",
        *_table(
            ("mode", "flag", "stream", "for"),
            (
                (f"`{OutputMode.HUMAN.value}`", "*(default)*", "stderr", "a person"),
                (f"`{OutputMode.JSON.value}`", "`--json`", "stdout", "an agent that parses"),
                (
                    f"`{OutputMode.DENSE.value}`",
                    "`--dense`",
                    "stdout",
                    "an agent that greps, or a bounded context",
                ),
            ),
        ),
        "",
        "Passing both flags is a `UsageError`: a run has exactly one output mode. `--dense`",
        "writes tab-separated records, header line first, and a tab or newline inside a cell",
        "becomes a space -- lossy on purpose, so that `cut -f2` and `wc -l` both work. Reach",
        "for `--json` when the exact bytes matter; reach for `--dense` when `rmspec ocr` on a",
        "432-page document would otherwise be megabytes of JSON.",
        "",
        "In both examples below every value is the field's **type**, not sample data.",
        "",
        "### Success envelope",
        "",
        f"`{API_VERSION_KEY}` and `{TYPE_KEY}` are always present -- branch on `{TYPE_KEY}`",
        f"before touching `{DATA_KEY}`. `{DEGRADATIONS_KEY}` is always present and `[]` when",
        "there were none; it is hoisted to the top level because every result carries one, and",
        f"it is order-preserving and never deduplicated. `{NEXT_ACTION_KEY}` appears only when",
        "there is an obvious next command.",
        "",
        *_fence("json", success),
        "",
        f"`{DATA_KEY}` carries its own `{DEGRADATIONS_KEY}` as well, and the difference is",
        "deliberate: the top-level tuple is everything that happened during the invocation,",
        f"document resolution included, while `{DATA_KEY}.{DEGRADATIONS_KEY}` is what the use",
        "case itself recorded. The top level is a superset, not a duplicate.",
        "",
        "### Failure envelope",
        "",
        f"`{ERROR_KEY}.type` is the error class name from the closed set below, and",
        f"`{ERROR_KEY}.exit_code` is the status the process also exits with -- carried inside",
        "the document so a caller needs one channel rather than two. `remediation` is always",
        "present and often `null`; `candidates` is dropped entirely unless the failure really",
        "searched, which today is `AmbiguousDocument` alone.",
        "",
        *_fence("json", failure),
    ]


def _command_index(commands: Sequence[Mapping[str, Any]], /) -> list[str]:
    """Build the one-line-per-command index.

    Parameters
    ----------
    commands
        The manifest's ``commands`` list.

    Returns
    -------
    list[str]
        The section's lines.
    """
    rows = [
        (
            f"`rmspec {command['name']}`",
            ", ".join(f"`{name}`" for name in command["response_types"] or ()),
            ", ".join(f"`{mode}`" for mode in command["modes"]),
            _inline(command["help"]),
        )
        for command in commands
    ]
    return [
        f"## Commands ({len(commands)})",
        "",
        "`response types` is every `type` discriminator the command's success envelope can",
        "carry, so the parser for `data` can be chosen before the call. Where a command lists",
        "two, a flag selects between them and that flag's row says which.",
        "",
        *_table(("command", "response types", "modes", "summary"), rows),
    ]


def _parameters(command: Mapping[str, Any], /) -> list[str]:
    """Build one command's parameter table, or nothing when it has no parameters of its own.

    Parameters
    ----------
    command
        One entry from the manifest's ``commands`` list.

    Returns
    -------
    list[str]
        The command's subsection, empty when every parameter it takes is an output flag.
    """
    rows = [
        (
            ", ".join(f"`{flag}`" for flag in parameter["flags"]),
            _code(parameter["type"]),
            "yes" if parameter["required"] else "no",
            _literal(parameter["default"]),
            ", ".join(_literal(choice) for choice in parameter["choices"] or ()),
            _inline(parameter["help"]),
        )
        for parameter in command["parameters"]
        if not OUTPUT_FLAGS.issuperset(parameter["flags"])
    ]
    if not rows:
        return []
    return [
        f"### `rmspec {command['name']}`",
        "",
        *_table(
            ("flags", "type", "required", "default", "choices", "help"),
            rows,
        ),
    ]


def _errors(errors: Sequence[Mapping[str, Any]], /) -> list[str]:
    """Build the closed failure-identity table and the exit-status key.

    Parameters
    ----------
    errors
        The manifest's ``errors`` list, in tree order with the root first.

    Returns
    -------
    list[str]
        The section's lines.
    """
    codes = sorted({int(entry["exit_code"]) for entry in errors})
    return [
        f"## Errors ({len(errors)} identities)",
        "",
        "Closed set. `abstract` marks a class with subclasses of its own: a caller normally",
        "meets one of its leaves, and the leaf's exit status may have been inherited from it,",
        "because the status is resolved by walking the class's own MRO.",
        "",
        *_table(
            ("exit", "meaning"),
            ((f"`{code}`", EXIT_CODE_MEANINGS[code]) for code in codes),
        ),
        "",
        *_table(
            ("type", "exit", "abstract"),
            (
                (
                    _code(entry["type"]),
                    f"`{entry['exit_code']}`",
                    "yes" if entry["abstract"] else "",
                )
                for entry in errors
            ),
        ),
    ]


def _degradations(kinds: Sequence[str], /) -> list[str]:
    """Build the closed degradation-kind list.

    Parameters
    ----------
    kinds
        The manifest's ``degradation_kinds`` list, in declaration order.

    Returns
    -------
    list[str]
        The section's lines.
    """
    return [
        f"## Degradations ({len(kinds)} kinds)",
        "",
        "A degradation is a thing the run did anyway, having substituted or skipped something --",
        "not a failure, and never swallowed. Closed set, so a caller can decide once per kind",
        "rather than matching strings. Each item carries",
        f"{', '.join(f'`{field}`' for field in Degradation.model_fields)}.",
        "",
        *(f"- `{kind}`" for kind in kinds),
    ]


def _settings(settings: Sequence[Mapping[str, Any]], /) -> list[str]:
    """Build the environment-variable table.

    Parameters
    ----------
    settings
        The manifest's ``settings`` list, in declaration order.

    Returns
    -------
    list[str]
        The section's lines.
    """
    rows = [
        (
            f"`{entry['name']}`",
            _code(entry["type"]),
            _literal(_home_relative(entry["default"])),
            _inline(entry["help"]),
        )
        for entry in settings
    ]
    return [
        f"## Settings ({len(settings)} variables)",
        "",
        "Read from the environment at startup. An unknown `RMSPEC_`-prefixed variable fails the",
        "run and names the closest match, so a typo cannot silently do nothing. `rmspec env`",
        "prints the resolved values as assignments a shell can `eval`; a default shown as a `~`",
        "path is resolved against the running user's home.",
        "",
        *_table(("variable", "type", "default", "help"), rows),
    ]


def _opening_examples(name: str, /) -> str:
    """Write the three calls that orient a caller, using the app's own name.

    Parameters
    ----------
    name
        The application name from the manifest, so the examples cannot name a binary that is
        not the one being described.

    Returns
    -------
    str
        Three shell lines, comments aligned.
    """
    calls = (
        (f"{name} manifest --json", "this document's source, machine-readable"),
        (f"{name} doctor --dense", "what the composed transport can and cannot do"),
        (f"{name} ls --json | jq '.data.documents[].uuid'", ""),
    )
    width = max(len(call) for call, _purpose in calls)
    return "\n".join(
        f"{call:<{width}}  # {purpose}" if purpose else call for call, purpose in calls
    )


def render_document(data: Mapping[str, Any], /) -> str:
    """Render the whole of ``AGENTS.md`` from one manifest payload.

    Parameters
    ----------
    data
        The ``data`` section of the ``manifest`` document, as
        :func:`~rmspec.cli._manifest.build_manifest` returns it.

    Returns
    -------
    str
        The document, ending in exactly one newline.
    """
    commands = data["commands"]
    sections: list[list[str]] = [
        [
            GENERATED_BANNER,
            "",
            f"# {data['name']} {data['version']} -- agent interface",
            "",
            "Humans and agents working on the same reMarkable tablet while it is on, over USB.",
            f"Every fact below is generated from `{data['name']} manifest --json`, which is the",
            "authoritative surface; when this file and that command disagree, the command wins",
            f"and someone forgot to run `{REGENERATE_TASK}`.",
            "",
            *_fence("bash", _opening_examples(str(data["name"]))),
        ],
        _conventions(),
        _modes_section(),
        _command_index(commands),
        *[_parameters(command) for command in commands],
        _errors(data["errors"]),
        _degradations(data["degradation_kinds"]),
        _settings(data["settings"]),
    ]
    body = "\n\n".join("\n".join(section) for section in sections if section)
    return f"{body}\n"


def _diff(expected: str, found: str, path: Path, /) -> str:
    """Describe how the committed document differs from the generated one.

    Parameters
    ----------
    expected
        The document the manifest implies.
    found
        The document on disk.
    path
        Where *found* was read from, for the diff header.

    Returns
    -------
    str
        A unified diff, ending in a newline.
    """
    lines = difflib.unified_diff(
        found.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{path} (committed)",
        tofile=f"{path} (from the manifest)",
    )
    return "".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Write ``AGENTS.md``, or report that the committed copy has drifted.

    Parameters
    ----------
    argv
        Command-line words, or ``None`` to read :data:`sys.argv`.

    Returns
    -------
    int
        ``0`` when the file was written or already matched, ``1`` when ``--check`` found a
        difference. The output path is an argument rather than a constant because this module
        lives inside an installed package and has no business guessing where the repository
        root is; ``mise.toml`` passes it, being the one file here that spells commands out.
    """
    parser = argparse.ArgumentParser(
        prog="rmspec-agents-md",
        description=render_document.__doc__,
    )
    parser.add_argument("output", type=Path, help="where to write the document")
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail when the committed document differs from the manifest",
    )
    arguments = parser.parse_args(argv)
    path: Path = arguments.output
    document = render_document(build_manifest(app))
    if not arguments.check:
        path.write_text(document, encoding="utf-8")
        return 0
    found = path.read_text(encoding="utf-8") if path.is_file() else ""
    if found == document:
        return 0
    sys.stderr.write(_diff(document, found, path))
    sys.stderr.write(f"\n{path} is out of date with `rmspec manifest`. Run `{REGENERATE_TASK}`.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
