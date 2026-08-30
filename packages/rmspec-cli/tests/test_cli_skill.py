"""``SKILL.md``: that every command and flag it names is real, and that the pointer resolves.

The document is prose and stays prose -- who does what on the tablet, what to confirm before
writing, and what a reader should deliberately not learn are judgements no introspection
produces. So this file does not check the judgement. It checks the half a reader can be *harmed*
by: an instruction to run a verb that does not exist, or to pass a flag the command never had.

That is not hypothetical. The first draft of this document said ``render --fmt pdf``; the flag is
``--format``, and the invocation fails with "Unknown option". The mistake was found by building
the flag universe below, before this file existed.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from rmspec.cli import app as root_app
from rmspec.cli._manifest import build_manifest
from rmspec.cli._skill import (
    SKILL_COMMAND,
    SKILL_FLAGS,
    SKILL_RESOURCE,
    describe_skill,
    digest,
    read,
    skill,
)

SHIPPED = Path(__file__).resolve().parents[1] / "src" / "rmspec" / "cli" / SKILL_RESOURCE

#: Spellings the document may use that no command declares, each because it is not a command's
#: flag at all. ``--help``, ``--version`` and ``--skill`` are pseudo-commands registered into
#: cyclopts' command mapping, so they appear in no parameter list by construction, and the
#: document has to be able to tell a reader not to scrape the first of them.
APP_LEVEL_FLAGS = frozenset({"--help", "-h", "--version", *SKILL_FLAGS})

#: ``rmspec <word>`` in the document, where the word is the start of an invocation.
_INVOCATION = re.compile(r"\brmspec ([a-z][a-z-]*)\b")

#: A long flag in the document, wherever it appears -- fenced block, table cell or backticks.
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")

_SUMMARY_FLOOR = 20
"""A one-line summary shorter than this is not one a caller could decide anything from."""


@pytest.fixture(name="document")
def _document() -> str:
    return read()


@pytest.fixture(name="manifest_data")
def _manifest_data() -> dict[str, Any]:
    return build_manifest(root_app)


def _command_names(manifest_data: dict[str, Any]) -> set[str]:
    """Every verb the manifest publishes, plus the first word of a grouped invocation."""
    names: set[str] = set()
    for command in manifest_data["commands"]:
        invocation = str(command["name"])
        names.add(invocation)
        # `device info` is one command reached by two words; a document naming `rmspec device`
        # is naming a real thing, so the group's word counts too.
        names.add(invocation.split(" ", maxsplit=1)[0])
    return names


def _flag_universe(manifest_data: dict[str, Any]) -> set[str]:
    """Every long flag any command accepts, positive and negative."""
    flags: set[str] = set()
    for command in manifest_data["commands"]:
        for parameter in command["parameters"]:
            flags.update(parameter.get("flags") or ())
            flags.update(parameter.get("negative_flags") or ())
    return {flag for flag in flags if flag.startswith("--")}


# ── the drift gate ──────────────────────────────────────────────────────────────────────


def test_the_document_names_some_commands_and_some_flags(document: str) -> None:
    """Guard the guard: two empty sweeps would make both assertions below vacuous."""
    assert len(set(_INVOCATION.findall(document))) > 5
    assert len(set(_FLAG.findall(document))) > 5


def test_every_invocation_the_document_teaches_is_a_real_command(
    document: str,
    manifest_data: dict[str, Any],
) -> None:
    taught = set(_INVOCATION.findall(document))
    real = _command_names(manifest_data) | APP_LEVEL_FLAGS

    assert taught <= real, (
        f"SKILL.md tells a reader to run {sorted(taught - real)}, which this CLI does not have. "
        f"The document is hand-written; `rmspec manifest --json` is the surface."
    )


def test_every_flag_the_document_teaches_is_a_real_flag(
    document: str,
    manifest_data: dict[str, Any],
) -> None:
    taught = set(_FLAG.findall(document))
    real = _flag_universe(manifest_data) | APP_LEVEL_FLAGS

    assert taught <= real, (
        f"SKILL.md names the flags {sorted(taught - real)}, which no command accepts. "
        f"`render --fmt` was exactly this mistake: the flag is `--format`."
    )


def test_every_command_the_document_mentions_in_prose_is_a_real_command(
    document: str,
    manifest_data: dict[str, Any],
) -> None:
    # The "who can do what" table names verbs in backticks rather than as invocations, and those
    # are the lines a reader skims first. A stale one there is as misleading as a stale recipe.
    backticked = set(re.findall(r"`([a-z][a-z ]*)`", document))
    verbs = _command_names(manifest_data)
    claimed = {word for word in backticked if word in verbs or f"{word} info" in verbs}

    assert claimed >= {"ls", "read", "ocr", "render", "diagram", "annotations", "push", "reply"}


# ── the document says what kind of document it is ───────────────────────────────────────


def test_the_document_sends_a_reader_to_the_generated_surface(document: str) -> None:
    # It is prose, and a reader who mistook it for the authoritative surface would trust a
    # sentence where they should have parsed a payload.
    assert "rmspec manifest --json" in document
    assert "written by hand, not generated" in document


def test_the_document_says_which_two_commands_cannot_be_undone(document: str) -> None:
    # The one thing in here that costs a person something if a reader misses it.
    assert "irreversible" in document
    assert "You cannot delete anything." in document


# ── the pointer, and the digest a caller caches against ─────────────────────────────────


def test_the_manifest_carries_a_pointer_rather_than_the_prose(
    manifest_data: dict[str, Any],
) -> None:
    pointer = manifest_data["skill"]

    assert pointer == describe_skill()
    assert pointer["command"] == SKILL_COMMAND
    assert len(str(pointer["summary"])) > _SUMMARY_FLOOR
    # The prose itself is deliberately absent: every machine consumer of the manifest would
    # otherwise pay for several kilobytes of a document written for a different reader.
    assert "The tablet stays on" not in str(manifest_data)


def test_the_pointer_names_an_invocation_that_exists(manifest_data: dict[str, Any]) -> None:
    words = str(manifest_data["skill"]["command"]).split(" ")

    assert words[0] == "rmspec"
    assert words[1] in APP_LEVEL_FLAGS


def test_the_digest_is_of_the_document_and_moves_only_when_it_does(document: str) -> None:
    expected = f"sha256:{hashlib.sha256(document.encode('utf-8')).hexdigest()}"

    assert digest() == expected
    assert digest() == describe_skill()["digest"]


def test_the_pseudo_command_is_not_in_the_operations_list(
    manifest_data: dict[str, Any],
) -> None:
    # `commands` means "operations a caller can perform". Printing a document is not one, which
    # is why --skill is reserved alongside --help and --version rather than listed beside `ocr`.
    assert not [c for c in manifest_data["commands"] if str(c["name"]).startswith("-")]


# ── the command itself ──────────────────────────────────────────────────────────────────


def test_the_flag_prints_the_document_on_stdout_and_nothing_on_stderr(
    capsys: pytest.CaptureFixture[str],
    document: str,
) -> None:
    # Raw Markdown, no envelope: the document is the payload. stderr stays empty so that
    # `rmspec --skill > SKILL.md` is a faithful copy.
    assert skill() == 0

    captured = capsys.readouterr()
    assert captured.out == document
    assert captured.err == ""


def test_the_document_read_from_the_package_is_the_committed_file() -> None:
    # importlib.resources is what lets an installed rmspec print this with no repository on the
    # machine; this pins that it resolves to the file under version control and not a copy.
    assert read() == SHIPPED.read_text(encoding="utf-8")


def test_the_document_ends_in_exactly_one_newline(document: str) -> None:
    assert document.endswith("\n")
    assert not document.endswith("\n\n")
