"""``rmspec reply``: the refusals that precede the write, the three modes, and the receipt.

**Nothing here reaches a tablet.** The owner's Paper Pro is attached and in use, and this is the
one command in the CLI that changes a page of somebody's handwriting -- so a test that got to the
wire would not merely be slow, it would edit a real page. ``SceneWriter`` and ``SceneAppender``
are bound over the real bindings with ``override=True``, and the writer double records every read
and every write, which is what makes "refused before anything was written" an assertion rather
than a hope.
"""

from __future__ import annotations

import io
import json as json_module
from typing import TYPE_CHECKING, Any

import pytest
from dishka import Provider, Scope, provide

from rmspec.app import ReplyOnPageResult
from rmspec.cli import _reply
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import API_VERSION
from rmspec.cli._reply import (
    DEFAULT_COLOUR,
    DENSE_COLUMNS,
    HUMAN_COLUMNS,
    REOPEN_SENTENCE,
    reply,
)
from rmspec.device.testing import InMemoryDeviceCatalog
from rmspec.domain.errors import (
    DeviceStateMismatchError,
    RmspecError,
    TransportKind,
)
from rmspec.domain.models import PenColor, Stroke
from rmspec.domain.ports.device import (
    DeviceCatalog,
    DeviceDocument,
    DeviceFileType,
    ScenePrecondition,
    SceneRead,
    SceneVisibility,
    SceneWriter,
    SceneWriteReceipt,
)
from rmspec.domain.ports.errors import DependencyProbe
from rmspec.domain.ports.formats import SceneAppender, SceneEdit
from rmspec.domain.ports.render import PhysicalSize

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rmspec.cli._invoke import Invoked

_OVERRIDDEN_PORTS = (SceneWriter, SceneAppender, DeviceCatalog, DependencyProbe)
"""The ports the doubles below bind, listed to give every import a runtime use.

dishka reads a provider's return annotation at container build to learn what it provides, so none
of these names may move into an ``if TYPE_CHECKING:`` block -- and this repo allows neither a
``noqa`` nor a ``type: ignore``. Same discipline as ``_container.BOUND_PORTS``.
"""

_USAGE_STATUS = 2
"""What a ``UsageError`` exits with, restated so a refusal test reads without a lookup."""

_UNAVAILABLE_STATUS = 69
"""``EX_UNAVAILABLE``, which ``DeviceError`` and every subclass of it score."""

_CONFIG_STATUS = 78
"""``EX_CONFIG``, which ``MissingDependencyError`` scores."""

_SOFTWARE_STATUS = 70
"""``EX_SOFTWARE``, which ``SceneRewriteUnsafe`` scores: this build will not rewrite these bytes.
"""

_SCENE = b"the human's ink"
_APPENDED = b"the human's ink, plus a reply"
_FINGERPRINT = "b" * 64
_LOCATION = "/x/doc-1/page-1.rm"
_SNAPSHOT = "/x/.cache/snapshots/doc-1/page-1/0"

# Written as code points because RUF001 rejects the literals, and because the code point is what
# the use case names in its own refusal.
_EM_DASH = chr(0x2014)
_ELLIPSIS = chr(0x2026)

_PROSE = f"A reply {_EM_DASH} with prose in it{_ELLIPSIS}"
"""Exactly the shape of an LLM sentence, and therefore the shape that fires the refusal."""


class _WriterDouble:
    """A ``SceneWriter`` over one page in memory, recording every call it was handed."""

    def __init__(
        self,
        *,
        scene: bytes | None = _SCENE,
        refuse: RmspecError | None = None,
    ) -> None:
        self._scene = scene
        self._refuse = refuse
        self.reads: list[tuple[str, str]] = []
        self.writes: list[tuple[ScenePrecondition, bytes]] = []

    def read_scene(self, doc_uuid: str, page_id: str, /) -> SceneRead:
        self.reads.append((doc_uuid, page_id))
        return SceneRead(
            doc_uuid=doc_uuid,
            page_id=page_id,
            location=_LOCATION,
            scene=self._scene,
        )

    def write_scene(self, precondition: ScenePrecondition, scene: bytes, /) -> SceneWriteReceipt:
        if self._refuse is not None:
            raise self._refuse
        self.writes.append((precondition, scene))
        return SceneWriteReceipt(
            doc_uuid=precondition.doc_uuid,
            page_id=precondition.page_id,
            location=_LOCATION,
            byte_count=len(scene),
            fingerprint=_FINGERPRINT,
            replaced=precondition.fingerprint,
            snapshot=_SNAPSHOT,
            visibility=SceneVisibility.REOPEN_REQUIRED,
        )

    def undo(self, receipt: SceneWriteReceipt, /) -> SceneWriteReceipt:
        raise NotImplementedError(receipt.page_id)


class _AppenderDouble:
    """A ``SceneAppender`` that concatenates and keeps the strokes it was given.

    Keeping them whole rather than counting them is what lets a test assert that a style flag
    reached the ink, which is the only place ``em_mm``, ``colour`` and ``thickness`` are
    observable -- none of them appears on the result.
    """

    def __init__(self) -> None:
        self.strokes: tuple[Stroke, ...] = ()

    def append_strokes(
        self,
        raw: bytes,
        _page_ref: str,
        /,
        *,
        strokes: tuple[Stroke, ...],
    ) -> SceneEdit:
        self.strokes = strokes
        return SceneEdit(scene=raw + _APPENDED[len(raw) :], author_id=9, layer_index=2)


class _ProbeDouble:
    """A ``DependencyProbe`` answering from one table."""

    def __init__(self, *, absent: frozenset[str] = frozenset()) -> None:
        self._absent = absent
        self.asked: list[str] = []

    def is_installed(self, module_name: str, /) -> bool:
        self.asked.append(module_name)
        return module_name not in self._absent

    def load_error(self, _module_name: str, /) -> str | None:
        return None


_TERMINAL_READ = "a terminal stdin must never be read"
"""Assigned rather than inline, because EM101 forbids a literal in a raise."""


class _TerminalStdin:
    """A stdin that is a terminal, so the no-argument path has nothing to read."""

    def isatty(self) -> bool:
        return True

    def read(self) -> str:
        raise AssertionError(_TERMINAL_READ)


class _RequestDoubles(Provider):
    """Bind the two request-scoped ports a reply touches."""

    scope = Scope.REQUEST

    def __init__(self, writer: _WriterDouble, catalog: InMemoryDeviceCatalog) -> None:
        super().__init__()
        self._writer = writer
        self._catalog = catalog

    @provide(override=True)
    def scene_writer(self) -> SceneWriter:
        return self._writer

    @provide(override=True)
    def device_catalog(self) -> DeviceCatalog:
        return self._catalog


class _AppDoubles(Provider):
    """Bind the two app-scoped ports whose real bindings load ``rmscene`` or probe modules."""

    scope = Scope.APP

    def __init__(self, appender: _AppenderDouble, probe: _ProbeDouble) -> None:
        super().__init__()
        self._appender = appender
        self._probe = probe

    @provide(override=True)
    def scene_appender(self) -> SceneAppender:
        return self._appender

    @provide(override=True)
    def dependency_probe(self) -> DependencyProbe:
        return self._probe


class _Rig:
    """One reply run's doubles, bound and readable afterwards."""

    def __init__(
        self,
        *,
        writer: _WriterDouble,
        appender: _AppenderDouble,
        catalog: InMemoryDeviceCatalog,
        probe: _ProbeDouble,
    ) -> None:
        self.writer = writer
        self.appender = appender
        self.catalog = catalog
        self.probe = probe


def _document(name: str, *, uuid: str = "doc-1") -> DeviceDocument:
    return DeviceDocument(
        uuid=uuid,
        name=name,
        file_type=DeviceFileType.NOTEBOOK,
        parent_uuid=None,
        page_count=3,
        trashed=False,
    )


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Rig]:
    """Return a factory that binds doubles into ``reply`` and hands them back.

    ``reply`` takes no ``providers=`` argument -- a real command never does -- so the factory
    wraps the ``run`` the module imported and appends the doubles to whatever it was passed.
    Every test therefore goes through the real ``reply`` signature, flags included.

    Returns
    -------
    Callable[..., _Rig]
        Keyword-only factory over the doubles' own options.
    """
    real_run = _reply.run

    def build(
        *,
        documents: Sequence[DeviceDocument] = (),
        scene: bytes | None = _SCENE,
        refuse: RmspecError | None = None,
        absent: frozenset[str] = frozenset(),
    ) -> _Rig:
        built = _Rig(
            writer=_WriterDouble(scene=scene, refuse=refuse),
            appender=_AppenderDouble(),
            catalog=InMemoryDeviceCatalog(documents=documents or (_document("Notes"),)),
            probe=_ProbeDouble(absent=absent),
        )
        doubles = (
            _RequestDoubles(built.writer, built.catalog),
            _AppDoubles(built.appender, built.probe),
        )

        def patched(
            body: Callable[[Invoked], int],
            /,
            *,
            json: bool = False,
            dense: bool = False,
            providers: Sequence[Provider] = (),
        ) -> int:
            return real_run(body, json=json, dense=dense, providers=(*providers, *doubles))

        monkeypatch.setattr(_reply, "run", patched)
        return built

    return build


@pytest.fixture
def piped(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Return a helper that puts one string on stdin as a pipe rather than a terminal.

    Returns
    -------
    Callable[[str], None]
        Called with the text a caller would have piped in.
    """

    def pipe(text: str) -> None:
        monkeypatch.setattr(_reply.sys, "stdin", io.StringIO(text))

    return pipe


def _envelope(captured: str) -> dict[str, Any]:
    document: dict[str, Any] = json_module.loads(captured)
    return document


def _result(
    *, substituted: tuple[str, ...] = (), snapshot: str | None = _SNAPSHOT
) -> ReplyOnPageResult:
    return ReplyOnPageResult(
        receipt=SceneWriteReceipt(
            doc_uuid="doc-1",
            page_id="page-1",
            location=_LOCATION,
            byte_count=len(_APPENDED),
            fingerprint=_FINGERPRINT,
            replaced=None if snapshot is None else "c" * 64,
            snapshot=snapshot,
            visibility=SceneVisibility.REOPEN_REQUIRED,
        ),
        author_id=9,
        layer_index=2,
        lines=("a reply",),
        stroke_count=7,
        extent_mm=PhysicalSize(width_mm=20.0, height_mm=6.0),
        substituted=substituted,
        degradations=(),
    )


# --------------------------------------------------------------------------- the message


def test_a_text_argument_is_taken_verbatim(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    assert reply("Notes", "hello there", page="page-1", json=True) == 0

    document = _envelope(capsys.readouterr().out)
    assert document["data"]["lines"] == ["hello there"]
    assert built.writer.writes


def test_the_reply_is_read_from_stdin_when_no_text_argument_is_given(
    rig: Callable[..., _Rig],
    piped: Callable[[str], None],
    capsys: pytest.CaptureFixture[str],
):
    # Stdin is the primary path, not the fallback: prose in a shell argument is what quoting
    # mangles, and the substitution report would then name characters nobody typed.
    rig()
    piped("a paragraph piped in")

    assert reply("Notes", page="page-1", json=True) == 0

    assert _envelope(capsys.readouterr().out)["data"]["lines"] == ["a paragraph piped in"]


def test_no_text_and_a_terminal_on_stdin_is_refused_rather_than_hanging(
    rig: Callable[..., _Rig],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    built = rig()
    monkeypatch.setattr(_reply.sys, "stdin", _TerminalStdin())

    assert reply("Notes", page="page-1", json=True) == _USAGE_STATUS

    document = _envelope(capsys.readouterr().out)
    assert document["error"]["type"] == "UsageError"
    assert "terminal on stdin" in document["error"]["message"]
    assert built.writer.reads == []


# ------------------------------------------------------------------- refusals before the wire


def test_prose_with_an_em_dash_is_refused_before_the_tablet_is_touched(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # The refusal that fires constantly in real use. Engraving is local and free, so this costs
    # zero round trips to discover -- which is why the writer was never even read from.
    built = rig()

    assert reply("Notes", _PROSE, page="page-1", json=True) == _USAGE_STATUS

    document = _envelope(capsys.readouterr().out)
    assert document["error"]["type"] == "UsageError"
    assert "U+2014" in document["error"]["message"]
    assert "U+2026" in document["error"]["message"]
    assert built.writer.reads == []
    assert built.writer.writes == []


def test_the_opt_in_draws_a_struck_box_and_records_one_degradation_per_character(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # Nothing folds: the em dash does not become "--". It becomes a box, and it is reported.
    rig()

    assert (
        reply(
            "Notes",
            _PROSE,
            page="page-1",
            allow_substituted_characters=True,
            json=True,
        )
        == 0
    )

    document = _envelope(capsys.readouterr().out)
    assert document["data"]["substituted"] == [_EM_DASH, _ELLIPSIS]
    kinds = [entry["kind"] for entry in document["degradations"]]
    assert kinds == ["ink_character_substituted", "ink_character_substituted"]
    assert "struck box" in document["degradations"][0]["detail"]
    assert document["degradations"][0]["substituted"] == "a struck box"


def test_strict_and_the_opt_in_together_are_refused_rather_than_one_winning_silently(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # One asks to proceed and record, the other to refuse rather than record. Picking a winner
    # quietly is how a page ends up full of boxes the caller thought it had forbidden.
    built = rig()

    status = reply(
        "Notes",
        _PROSE,
        page="page-1",
        allow_substituted_characters=True,
        strict=True,
        json=True,
    )

    assert status == _USAGE_STATUS
    document = _envelope(capsys.readouterr().out)
    assert document["error"]["type"] == "UsageError"
    assert "--allow-substituted-characters" in document["error"]["message"]
    assert built.probe.asked == []
    assert built.writer.reads == []


def test_a_whitespace_only_reply_is_refused(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    built = rig()

    assert reply("Notes", "   \n ", page="page-1", json=True) == _USAGE_STATUS

    assert _envelope(capsys.readouterr().out)["error"]["type"] == "UsageError"
    assert built.writer.reads == []


def test_a_reply_that_would_run_off_the_page_is_refused_before_the_wire(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # Knowable for free from the extent the engraver reported, which is why it is not discovered
    # by looking at the tablet. Ink below the bottom edge is the same defect as invisible text.
    built = rig()

    status = reply("Notes", "far too low", page="page-1", top_mm=239.0, json=True)

    assert status == _USAGE_STATUS
    assert "on the page" in _envelope(capsys.readouterr().out)["error"]["message"]
    assert built.writer.reads == []


def test_a_page_the_device_stores_no_scene_for_is_not_invented(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    built = rig(scene=None)

    assert reply("Notes", "hello", page="page-1", json=True) == _SOFTWARE_STATUS

    document = _envelope(capsys.readouterr().out)
    assert document["error"]["type"] == "SceneRewriteUnsafe"
    assert built.writer.reads == [("doc-1", "page-1")]
    assert built.writer.writes == []


def test_a_missing_optional_module_refuses_before_the_document_is_resolved(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    built = rig(absent=frozenset({"paramiko"}))

    assert reply("Notes", "hello", page="page-1", json=True) == _CONFIG_STATUS

    assert _envelope(capsys.readouterr().out)["error"]["type"] == "MissingDependencyError"
    assert built.writer.reads == []


def test_a_page_the_human_drew_on_between_the_read_and_the_write_is_refused_not_merged(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # The project's differentiator. A user seeing this needs to understand it protected their
    # strokes: nothing was written, and re-reading is the whole recovery.
    rig(
        refuse=DeviceStateMismatchError(
            transport=TransportKind.SSH,
            subject="page page-1 of document doc-1",
            expected="sha256 " + "d" * 64,
            observed="sha256 " + "e" * 64,
            retryable=True,
        )
    )

    assert reply("Notes", "hello", page="page-1", json=True) == _UNAVAILABLE_STATUS

    document = _envelope(capsys.readouterr().out)
    assert document["error"]["type"] == "DeviceStateMismatchError"
    assert document["error"]["remediation"] == (
        "re-read page page-1 of document doc-1 and repeat the operation"
    )


# ------------------------------------------------------------------------------ the request


def test_the_document_selector_is_resolved_and_the_page_is_passed_through(
    rig: Callable[..., _Rig],
):
    built = rig(documents=(_document("Meeting notes", uuid="doc-9"),))

    assert reply("Meeting", "hello", page="page-4", json=True) == 0

    assert built.writer.reads == [("doc-9", "page-4")]
    assert built.writer.writes[0][0].doc_uuid == "doc-9"
    assert built.writer.writes[0][0].page_id == "page-4"


def test_an_ambiguous_selector_records_a_degradation_by_default(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    rig(documents=(_document("Notes", uuid="doc-1"), _document("Notes", uuid="doc-2")))

    assert reply("Notes", "hello", page="page-1", json=True) == 0

    kinds = [entry["kind"] for entry in _envelope(capsys.readouterr().out)["degradations"]]
    assert "ambiguous_auto_resolved" in kinds


def test_strict_refuses_an_ambiguous_selector_instead_of_taking_the_ranked_winner(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    built = rig(documents=(_document("Notes", uuid="doc-1"), _document("Notes", uuid="doc-2")))

    assert reply("Notes", "hello", page="page-1", strict=True, json=True) == _USAGE_STATUS

    assert _envelope(capsys.readouterr().out)["error"]["type"] == "AmbiguousDocument"
    assert built.writer.reads == []


def test_the_thickness_setting_reaches_the_ink_when_no_flag_overrides_it(
    rig: Callable[..., _Rig],
    monkeypatch: pytest.MonkeyPatch,
):
    # One setting calibrates rendering and replying together, so the slider is not a bare
    # constant retyped in a command signature.
    monkeypatch.setenv("RMSPEC_THICKNESS", "3.0")
    thick = rig()
    assert reply("Notes", "iiii", page="page-1", json=True) == 0

    monkeypatch.setenv("RMSPEC_THICKNESS", "1.0")
    thin = rig()
    assert reply("Notes", "iiii", page="page-1", json=True) == 0

    assert {stroke.thickness_scale for stroke in thick.appender.strokes} == {3.0}
    assert {stroke.thickness_scale for stroke in thin.appender.strokes} == {1.0}


def test_an_explicit_thickness_overrides_the_setting(
    rig: Callable[..., _Rig],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("RMSPEC_THICKNESS", "1.5")
    built = rig()

    assert reply("Notes", "hello", page="page-1", thickness=4.0, json=True) == 0

    assert {stroke.thickness_scale for stroke in built.appender.strokes} == {4.0}


def test_the_default_colour_can_be_told_apart_from_black_handwriting():
    # The domain's own field docstring says the colour exists so a reply "can be told from it at
    # a glance", which a black reply on a page of black ink cannot.
    assert DEFAULT_COLOUR is PenColor.BLUE
    assert DEFAULT_COLOUR is not PenColor.BLACK


def test_a_colour_flag_reaches_the_strokes(rig: Callable[..., _Rig]):
    black = rig()
    assert reply("Notes", "o", page="page-1", colour=PenColor.BLACK, json=True) == 0
    red = rig()
    assert reply("Notes", "o", page="page-1", colour=PenColor.RED, json=True) == 0
    default = rig()
    assert reply("Notes", "o", page="page-1", json=True) == 0

    assert {stroke.color for stroke in black.appender.strokes} == {PenColor.BLACK}
    assert {stroke.color for stroke in red.appender.strokes} == {PenColor.RED}
    assert {stroke.color for stroke in default.appender.strokes} == {DEFAULT_COLOUR}


def test_placement_is_millimetres_from_the_top_left_and_no_flag_mentions_x_shift():
    # The centre-origin correction is applied inside the engraver, so a caller measures from the
    # corner of the sheet it is looking at and never adds half a page width -- or adds it twice.
    names = set(reply.__annotations__)

    assert {"left_mm", "top_mm", "width_mm"} <= names
    assert not any("shift" in name for name in names)
    assert "x_shift" not in (reply.__doc__ or "")


# ----------------------------------------------------------------------------- the envelope


def test_the_json_envelope_carries_the_receipt_whole_and_unflattened(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # The receipt is the undo token. A caller reassembling it from sibling fields is a caller
    # who can transpose two of them and restore the wrong snapshot over the wrong page.
    rig()

    assert reply("Notes", "hello", page="page-1", json=True) == 0

    document = _envelope(capsys.readouterr().out)
    assert document["api_version"] == API_VERSION
    assert document["type"] == RESPONSE_TYPES["reply"]
    assert document["data"]["receipt"] == {
        "doc_uuid": "doc-1",
        "page_id": "page-1",
        "location": _LOCATION,
        "byte_count": len(_APPENDED),
        "fingerprint": _FINGERPRINT,
        "replaced": document["data"]["receipt"]["replaced"],
        "snapshot": _SNAPSHOT,
        "visibility": "reopen_required",
    }
    assert set(document["data"]) == {
        "receipt",
        "author_id",
        "layer_index",
        "lines",
        "stroke_count",
        "extent_mm",
        "substituted",
        "degradations",
    }


def test_the_envelope_never_claims_the_human_can_already_see_the_reply(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    rig()

    assert reply("Notes", "hello", page="page-1", json=True) == 0

    document = _envelope(capsys.readouterr().out)
    assert document["data"]["receipt"]["visibility"] == SceneVisibility.REOPEN_REQUIRED.value
    assert list(SceneVisibility) == [SceneVisibility.REOPEN_REQUIRED]
    assert "reopen" in document["next"]["purpose"]


def test_the_next_action_is_a_runnable_shell_line_with_the_selector_quoted(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # NextAction.command is documented as "the exact shell line to run, ready to execute. Never
    # a paraphrase of one" -- so the reopen instruction rides in `purpose`, which is prose.
    rig(documents=(_document("Quick sheets"),))

    assert reply("Quick sheets", "hello", page="page-1", json=True) == 0

    action = _envelope(capsys.readouterr().out)["next"]
    assert action["command"] == "rmspec render 'Quick sheets' reply-page-1.svg"
    assert REOPEN_SENTENCE in action["purpose"]


def test_the_top_level_degradations_are_a_superset_of_the_results_own(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # The envelope's tuple is everything that happened during the invocation, resolution
    # included; data.degradations is what the use case itself recorded.
    rig(documents=(_document("Notes", uuid="doc-1"), _document("Notes", uuid="doc-2")))

    status = reply(
        "Notes",
        _PROSE,
        page="page-1",
        allow_substituted_characters=True,
        json=True,
    )

    assert status == 0
    document = _envelope(capsys.readouterr().out)
    inner = {entry["kind"] for entry in document["data"]["degradations"]}
    outer = {entry["kind"] for entry in document["degradations"]}
    assert inner == {"ink_character_substituted"}
    assert outer == {"ink_character_substituted", "ambiguous_auto_resolved"}
    assert inner < outer


# -------------------------------------------------------------------------------- the modes


def test_dense_writes_one_tab_separated_record_to_stdout(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    rig()

    assert reply("Notes", "hello", page="page-1", dense=True) == 0

    captured = capsys.readouterr()
    header, record = captured.out.splitlines()
    assert header.split("\t") == list(DENSE_COLUMNS)
    cells = record.split("\t")
    assert cells[0] == "doc-1"
    assert cells[1] == "page-1"
    assert cells[DENSE_COLUMNS.index("visibility")] == "reopen_required"
    assert cells[DENSE_COLUMNS.index("substituted")] == "-"
    assert cells[DENSE_COLUMNS.index("snapshot")] == _SNAPSHOT


def test_dense_names_every_substituted_character_in_one_cell(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    rig()

    status = reply(
        "Notes",
        _PROSE,
        page="page-1",
        allow_substituted_characters=True,
        dense=True,
    )

    assert status == 0
    captured = capsys.readouterr()
    cells = captured.out.splitlines()[1].split("\t")
    assert cells[DENSE_COLUMNS.index("substituted")] == f"{_EM_DASH},{_ELLIPSIS}"
    assert "ink_character_substituted" in captured.err


def test_the_default_mode_writes_a_table_to_stderr_and_nothing_to_stdout(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # Three branches, never two. HUMAN renders a rich renderable through display(), which goes
    # to stderr; only DENSE writes records to stdout.
    rig()

    assert reply("Notes", "hello", page="page-1") == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "reply written" in captured.err
    assert "reopen it" in captured.err


def test_the_human_table_is_a_narrower_projection_of_the_same_record():
    # Taken by index into the same row tuple, which is what stops the two views drifting.
    row = _reply._row(_result())
    table = _reply._table(row)

    assert len(row) == len(DENSE_COLUMNS)
    assert len(HUMAN_COLUMNS) < len(DENSE_COLUMNS)
    assert set(HUMAN_COLUMNS) <= set(range(len(DENSE_COLUMNS)))
    assert table.row_count == len(HUMAN_COLUMNS)
    assert table.caption == REOPEN_SENTENCE


def test_a_receipt_that_superseded_nothing_still_renders_a_readable_cell():
    # Unreachable through the command -- ReplyOnPage refuses a page with no scene -- but the row
    # builder is a public projection of a public model, and a blank cell reads as a mis-cut.
    row = _reply._row(_result(snapshot=None))

    assert row[DENSE_COLUMNS.index("snapshot")] == "-"


def test_the_extent_is_reported_so_the_next_reply_can_start_below_this_one(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # Measured from the requested corner, not from the ink's own bounds, so `top_mm + height_mm`
    # is where the first reply actually ended.
    rig()

    assert reply("Notes", "hello", page="page-1", top_mm=20.0, em_mm=5.0, json=True) == 0

    extent = _envelope(capsys.readouterr().out)["data"]["extent_mm"]
    # 20.0 + 5.0 is where the ink's descender line actually sits: the box is measured from the
    # corner the caller asked for, not from the ink's own top, which is a whole ascender lower.
    assert extent["height_mm"] == pytest.approx(5.0)
    assert extent["width_mm"] == pytest.approx(9.4)


def test_two_lines_are_reported_as_they_were_wrapped(
    rig: Callable[..., _Rig],
    capsys: pytest.CaptureFixture[str],
):
    # Wrapping is the engraver's decision and the caller's to disagree with, so the lines the
    # page actually shows are reported rather than the string that was handed in.
    rig()

    assert reply("Notes", "wrap this reply", page="page-1", width_mm=20.0, json=True) == 0

    lines = _envelope(capsys.readouterr().out)["data"]["lines"]
    assert len(lines) > 1
    assert " ".join(lines) == "wrap this reply"
