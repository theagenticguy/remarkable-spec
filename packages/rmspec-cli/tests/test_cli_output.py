"""Output discipline: stdout stays parseable, stderr carries the human, exits come from domain."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
from rich.console import Console

from rmspec.cli._output import (
    API_VERSION,
    NEXT_ACTION_KEY,
    RESERVED_ENVELOPE_KEYS,
    CliOutput,
    ErrorEnvelope,
    NextAction,
    OutputMode,
    make_console_pair,
    resolve_mode,
)
from rmspec.domain.errors import (
    AmbiguousDocument,
    AuditWriteFailedError,
    Degradation,
    DegradationKind,
    DocumentCandidate,
    InvalidSettingError,
    UsageError,
    exit_code,
)

_LONG_NAME = (
    "Notes [draft] 2026 architecture review with a trailing title long enough to cross "
    "the eighty column default that rich uses whenever its file is not a terminal"
)
"""A value that reproduces both legacy corruptions at once: console markup and wrapping."""

_EX_USAGE = 2
_EX_CONFIG = 78
_EX_IO_ERROR = 74


@dataclass(frozen=True, slots=True)
class _Captured:
    """One :class:`CliOutput` and the two buffers behind it."""

    out: CliOutput
    stdout: io.StringIO
    stderr: io.StringIO

    @property
    def machine(self) -> str:
        """Everything written to stdout.

        Returns
        -------
        str
            The raw stdout text.
        """
        return self.stdout.getvalue()

    @property
    def human(self) -> str:
        """Everything written to stderr.

        Returns
        -------
        str
            The raw stderr text.
        """
        return self.stderr.getvalue()


def _captured(*, mode: OutputMode) -> _Captured:
    stdout = io.StringIO()
    stderr = io.StringIO()
    consoles = make_console_pair(stdout=stdout, stderr=stderr)
    return _Captured(
        out=CliOutput(consoles=consoles, mode=mode),
        stdout=stdout,
        stderr=stderr,
    )


def _degradation(subject: str, /) -> Degradation:
    return Degradation(
        kind=DegradationKind.AUDIT_NOT_RECORDED,
        subject=subject,
        detail="the store was locked",
    )


# ───────────────────────── the three modes ─────────────────────────


def test_the_three_modes_have_the_names_the_contract_froze():
    assert [mode.value for mode in OutputMode] == ["human", "json", "dense"]


def test_mode_is_the_only_branch_a_command_needs():
    # Was `test_machine_readable_is_the_only_branch_a_command_needs`, written when there
    # were two renderings and one boolean. There are three now, so the honest invariant is
    # that ONE value still distinguishes them -- a command branches on `mode` once and
    # never re-checks -- and that `machine_readable` still means exactly "the JSON envelope
    # was asked for", so no existing call site changed meaning.
    for mode in OutputMode:
        out = _captured(mode=mode).out

        assert out.mode is mode
        assert [candidate for candidate in OutputMode if out.mode is candidate] == [mode]
        assert out.machine_readable is (mode is OutputMode.JSON)


def test_dense_is_machine_consumable_but_is_not_the_json_envelope():
    # `machine_readable` guards `emit`, and a DENSE run must not be handed an envelope: it
    # is a different document with different keys.
    out = _captured(mode=OutputMode.DENSE).out

    assert out.mode is OutputMode.DENSE
    assert out.machine_readable is False


def test_neither_flag_is_human_and_each_flag_alone_is_itself():
    assert resolve_mode(json=False, dense=False) is OutputMode.HUMAN
    assert resolve_mode(json=True, dense=False) is OutputMode.JSON
    assert resolve_mode(json=False, dense=True) is OutputMode.DENSE


def test_both_flags_is_a_domain_usage_error_so_no_command_describes_it_itself():
    with pytest.raises(UsageError) as raised:
        resolve_mode(json=True, dense=True)

    assert "--json and --dense" in raised.value.message
    assert raised.value.remediation is not None
    assert exit_code(raised.value) == _EX_USAGE


# ───────────────────────── the success envelope ─────────────────────────


def test_the_frame_is_always_present_and_the_payload_goes_under_data():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit({"served": ["DeviceCatalog"], "count": 1}, response_type="capabilities")

    assert json.loads(captured.machine) == {
        "api_version": API_VERSION,
        "type": "capabilities",
        "data": {"served": ["DeviceCatalog"], "count": 1},
        "degradations": [],
    }
    assert captured.human == ""


def test_every_top_level_key_is_one_the_frame_declared():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit(
        {"ok": True},
        response_type="catalog",
        next_action=NextAction(command="rmspec ls", purpose="list again"),
    )

    assert set(json.loads(captured.machine)) <= RESERVED_ENVELOPE_KEYS


def test_a_payload_key_named_next_is_a_key_and_not_a_collision():
    # The old flat shape merged the payload into the top level, so `next` had to be
    # reserved by convention and policed by a test. Nesting under `data` makes the
    # collision structurally impossible instead: every reserved name is now a legal
    # payload key that round-trips untouched.
    captured = _captured(mode=OutputMode.JSON)
    payload = {key: f"a value called {key}" for key in sorted(RESERVED_ENVELOPE_KEYS)}

    captured.out.emit(payload, response_type="catalog")

    document = json.loads(captured.machine)
    assert document["data"] == payload
    assert document["type"] == "catalog"
    assert NEXT_ACTION_KEY not in document


def test_degradations_are_hoisted_to_the_top_level_and_serialised():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit(
        {"pages": 1},
        response_type="transcription",
        degradations=(_degradation("doc-1"),),
    )

    assert json.loads(captured.machine)["degradations"] == [
        {
            "kind": DegradationKind.AUDIT_NOT_RECORDED.value,
            "subject": "doc-1",
            "detail": "the store was locked",
            "substituted": None,
        }
    ]


def test_degradations_keep_their_order_and_are_never_deduplicated():
    # Collapsing duplicates is a presentation choice and belongs in HUMAN mode only: two
    # pages degrading the same way is two facts, not one.
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit(
        {"pages": 3},
        response_type="transcription",
        degradations=(_degradation("doc-1"), _degradation("doc-1"), _degradation("doc-2")),
    )

    reported = json.loads(captured.machine)["degradations"]
    assert [item["subject"] for item in reported] == ["doc-1", "doc-1", "doc-2"]


def test_a_markup_looking_value_survives_and_is_not_wrapped():
    # console.print(json.dumps(...)) deleted "[draft]" as console markup and hard-wrapped
    # the string at 80 columns, which is how `device info --json` and `device ls --json`
    # were corrupted. print_json passes soft_wrap=True and builds a JSON renderable, so
    # neither happens.
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit({"name": _LONG_NAME}, response_type="document")

    assert json.loads(captured.machine)["data"] == {"name": _LONG_NAME}
    assert "[draft]" in captured.machine


def test_the_anti_pattern_this_replaces_really_does_corrupt_the_payload():
    # The witness for the paragraph above, so the rule is not folklore.
    buffer = io.StringIO()
    Console(file=buffer, width=20, no_color=True, force_terminal=False).print(
        json.dumps({"name": _LONG_NAME})
    )

    assert "[draft]" not in buffer.getvalue()
    with pytest.raises(json.JSONDecodeError):
        json.loads(buffer.getvalue())


def test_no_escape_codes_reach_stdout_even_when_force_color_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    # FORCE_COLOR=3 is set in at least one environment this project is developed in, and a
    # Console honours it even when its file is a pipe.
    monkeypatch.setenv("FORCE_COLOR", "3")
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit({"name": "plain"}, response_type="document")

    assert "\x1b" not in captured.machine


def test_a_next_action_carries_the_literal_command():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.emit(
        {"ok": True},
        response_type="capabilities",
        next_action=NextAction(command="uv sync --extra render", purpose="install cairo"),
    )

    document = json.loads(captured.machine)
    assert document[NEXT_ACTION_KEY] == {
        "command": "uv sync --extra render",
        "purpose": "install cairo",
    }


def test_a_next_action_will_not_describe_a_command_instead_of_being_one():
    with pytest.raises(ValueError, match="at least 1 character"):
        NextAction(command="", purpose="install cairo")


# ───────────────────────── DENSE ─────────────────────────


def test_dense_writes_the_header_first_then_one_tab_separated_record_per_line():
    captured = _captured(mode=OutputMode.DENSE)

    captured.out.rows(
        ("uuid", "name"),
        [("aaaaaaaa-0000", "Notes A"), ("bbbbbbbb-0000", "Notes B")],
    )

    assert captured.machine == ("uuid\tname\naaaaaaaa-0000\tNotes A\nbbbbbbbb-0000\tNotes B\n")
    assert captured.human == ""


def test_the_header_is_written_even_when_there_are_no_records():
    # A consumer can always read the shape, so an empty result is not indistinguishable
    # from a command that wrote nothing at all.
    captured = _captured(mode=OutputMode.DENSE)

    captured.out.rows(("uuid", "name"), ())

    assert captured.machine == "uuid\tname\n"


def test_dense_records_go_through_the_plain_writer_so_a_long_cell_is_not_wrapped():
    # The same two defects that corrupted the JSON would break the record stream: rich
    # wraps at 80 columns off a tty and eats [draft] as markup.
    captured = _captured(mode=OutputMode.DENSE)

    captured.out.rows(("uuid", "name"), [("aaaa", _LONG_NAME)])

    assert captured.machine == f"uuid\tname\naaaa\t{_LONG_NAME}\n"


def test_a_tab_or_newline_in_a_cell_becomes_one_space_so_cut_and_wc_keep_working():
    # Deliberately lossy: the whole value of the format is that `cut -f2` splits on tab and
    # `wc -l` counts records. \r\n collapses to ONE space, not two.
    captured = _captured(mode=OutputMode.DENSE)

    captured.out.rows(("page", "text"), [("1", "a\tb\nc\r\nd\re")])

    lines = captured.machine.splitlines()
    assert lines == ["page\ttext", "1\ta b c d e"]
    assert len(lines[1].split("\t")) == 2


def test_dense_consumes_a_generator_so_a_large_result_is_never_materialised_twice():
    captured = _captured(mode=OutputMode.DENSE)

    captured.out.rows(("page_index",), ((str(index),) for index in range(3)))

    assert captured.machine == "page_index\n0\n1\n2\n"


# ───────────────────────── the human stream ─────────────────────────


def test_a_plain_line_is_written_verbatim_so_eval_works():
    # rich wraps at 80 columns off a tty and eats [...] as markup, so `eval "$(rmspec
    # env)"` broke on any path over about 65 characters.
    captured = _captured(mode=OutputMode.HUMAN)
    assignment = f"export RMSPEC_XOCHITL='/Users/someone/Library/Application Support/{_LONG_NAME}'"

    captured.out.line(assignment)

    assert captured.machine == f"{assignment}\n"


def test_human_output_goes_to_stderr_only():
    captured = _captured(mode=OutputMode.HUMAN)

    captured.out.display("a table would go here")

    assert captured.machine == ""
    assert "a table would go here" in captured.human


def test_warnings_go_to_stderr_only():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.warn("the search index was stale")

    assert captured.machine == ""
    assert "warning: the search index was stale" in captured.human


def test_no_degradations_prints_nothing():
    captured = _captured(mode=OutputMode.HUMAN)

    captured.out.report_degradations(())

    assert captured.human == ""


def test_each_degradation_is_summarised_on_stderr():
    captured = _captured(mode=OutputMode.HUMAN)

    captured.out.report_degradations((_degradation("doc-1"),))

    assert "doc-1" in captured.human
    assert "the store was locked" in captured.human
    assert captured.machine == ""


# ───────────────────────── the failure envelope ─────────────────────────


def test_a_failure_in_json_mode_is_a_typed_envelope_on_stdout():
    captured = _captured(mode=OutputMode.JSON)
    err = InvalidSettingError(
        setting="RMSPEC_RENDER_DPI",
        value="0",
        requirement="greater than 0",
    )

    status = captured.out.fail(err)

    assert status == _EX_CONFIG
    assert json.loads(captured.machine) == {
        "api_version": API_VERSION,
        "type": "error",
        "error": {
            "type": "InvalidSettingError",
            "message": err.message,
            "remediation": err.remediation,
            "exit_code": _EX_CONFIG,
        },
    }
    assert "error: " in captured.human


def test_the_documents_exit_code_is_the_same_number_the_process_returns():
    # Carried inside the document so an agent does not need a second channel, and taken
    # from the domain's own MRO walk so it can never be a second opinion.
    for err in (
        UsageError(subject="the query", requirement="a non-empty string"),
        AuditWriteFailedError(detail="the store was locked"),
        InvalidSettingError(setting="RMSPEC_RENDER_DPI", value="0", requirement="above 0"),
    ):
        captured = _captured(mode=OutputMode.JSON)

        status = captured.out.fail(err)

        assert json.loads(captured.machine)["error"]["exit_code"] == status == exit_code(err)


def test_the_error_type_is_the_class_name_and_no_second_vocabulary_is_invented():
    err = AuditWriteFailedError(detail="the store was locked")

    assert ErrorEnvelope.of(err).type == err.code == "AuditWriteFailedError"


def test_a_failure_in_human_mode_leaves_stdout_empty():
    captured = _captured(mode=OutputMode.HUMAN)
    err = UsageError(subject="the query", requirement="a non-empty string")

    status = captured.out.fail(err)

    assert status == _EX_USAGE
    assert captured.machine == ""
    assert err.message in captured.human
    assert "try: " in captured.human


def test_a_failure_in_dense_mode_keeps_stdout_a_pure_record_stream():
    # Appending a JSON object to a half-written record stream would break the consumer
    # that was reading it with `cut`. The exit status plus the stderr sentence is the
    # dense failure contract; a caller wanting structure asks for --json.
    captured = _captured(mode=OutputMode.DENSE)
    captured.out.rows(("page", "text"), [("1", "hello")])
    err = UsageError(subject="the query", requirement="a non-empty string")

    status = captured.out.fail(err)

    assert status == _EX_USAGE
    assert captured.machine == "page\ttext\n1\thello\n"
    assert err.message in captured.human


def test_an_error_with_no_remediation_says_null_and_offers_no_advice():
    captured = _captured(mode=OutputMode.JSON)

    status = captured.out.fail(AuditWriteFailedError(detail="the store was locked"))

    assert status == _EX_IO_ERROR
    assert json.loads(captured.machine)["error"]["remediation"] is None
    assert "try: " not in captured.human


def test_candidates_are_rendered_on_both_streams_when_the_failure_carries_them():
    captured = _captured(mode=OutputMode.JSON)
    err = AmbiguousDocument(
        query="notes",
        candidates=(
            DocumentCandidate(uuid="aaaaaaaa-0000-0000-0000-000000000000", name="Notes A"),
            DocumentCandidate(uuid="bbbbbbbb-0000-0000-0000-000000000000", name="Notes B"),
        ),
    )

    status = captured.out.fail(err)

    assert status == _EX_USAGE
    assert json.loads(captured.machine)["error"]["candidates"] == [
        {"uuid": "aaaaaaaa-0000-0000-0000-000000000000", "name": "Notes A"},
        {"uuid": "bbbbbbbb-0000-0000-0000-000000000000", "name": "Notes B"},
    ]
    assert "Notes B" in captured.human


def test_a_failure_may_carry_the_command_that_fixes_it():
    captured = _captured(mode=OutputMode.JSON)

    captured.out.fail(
        UsageError(subject="the query", requirement="a non-empty string"),
        next_action=NextAction(command="rmspec search notes", purpose="retry with a query"),
    )

    assert json.loads(captured.machine)[NEXT_ACTION_KEY]["command"] == "rmspec search notes"


def test_the_envelope_omits_candidates_rather_than_claiming_an_empty_search():
    envelope = ErrorEnvelope.of(UsageError(subject="the query", requirement="a string"))

    assert "candidates" not in envelope.as_document()
