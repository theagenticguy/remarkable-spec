"""``rmspec ocr``: transcribe pages through four tiers, and say which ones were paid for.

What this command reports, and why each field is here
----------------------------------------------------
:class:`~rmspec.app.TranscribePages` decides how far up the ladder each page has to go, and
the interesting part of its answer is not only the text. ``tier_reached`` and
``short_circuited`` exist because an agent wants to know whether it paid, and because a
short-circuit rate is the only way to tell whether the tablet's free tier-0 reading is
earning its keep. ``cached`` says the row came from
:class:`~rmspec.domain.ports.persistence.OcrCache`, and ``cached_provenance`` is how a
reader learns which tier produced *that row* -- ``tier_reached`` stays ``0`` for a hit
because it describes what **this** run paid. ``recognizer_failures`` is data, never an
error: one engine's outage must not discard another's output, and only a total failure is
:class:`~rmspec.domain.errors.AllRecognizersFailed`. All of it is projected into ``DENSE``
as well as ``--json``, because the run that most wants to know what it cost is the run
whose context is bounded.

The density here is ``RMSPEC_OCR_DPI``, and that is not ``RMSPEC_RENDER_DPI``
--------------------------------------------------------------------------
:attr:`~rmspec.cli._settings.CliSettings.ocr_dpi` is 300, the density the recognisers were
tuned against and a measurement of no panel at all;
:attr:`~rmspec.cli._settings.CliSettings.render_dpi` is 229, the Paper Pro panel's own
density and therefore a 1:1 export. They are different quantities for different jobs and
conflating them is the exact defect the settings split fixed, so this module reads only the
first and ``rmspec render`` reads only the second. It is also required: the request
validator refuses a transcription whose render produces no pixels, because every tier below
tier 0 reads pixels and the cache key needs their digest.

The probe covers what this composition will actually use
-------------------------------------------------------
Scene decoding and rasterizing always, then one feature per engine
``RMSPEC_OCR_ENGINES`` selected -- not both, because a Textract-only run must not be told
to install ``pyobjc``, and ``apple_vision`` is macOS-only -- and finally the Bedrock
feature, since tiers 2 and 3 are model calls. The engine features are taken in
:class:`~rmspec.cli._settings.OcrEngineName` declaration order, which is the order the
recognisers run in, so two runs of the same command report the same first failure.

The cap is the entry boundary
-----------------------------
``RMSPEC_MAX_PAGES`` (64) reaches
:meth:`~rmspec.app.PageSelection.resolve_against`, which enforces it before any render,
raster or model call. That is why one 432-page document cannot silently become 432 model
calls, and why the request's ``max_pages`` has no default for a command to forget.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Final

from cyclopts import Parameter
from rich.table import Table

from rmspec.app import RenderPagesRequest, TranscribePages, TranscribePagesRequest
from rmspec.cli._invoke import (
    FEATURE_MODEL_BEDROCK,
    FEATURE_OCR_APPLE_VISION,
    FEATURE_OCR_TEXTRACT,
    FEATURE_RASTER,
    FEATURE_SCENE_DECODE,
    DenseFlag,
    JsonFlag,
    LimitOption,
    MaxPagesOption,
    PagesOption,
    StrictFlag,
    run,
)
from rmspec.cli._manifest import RESPONSE_TYPES
from rmspec.cli._output import OutputMode
from rmspec.cli._settings import OcrEngineName
from rmspec.domain.errors import UsageError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from rmspec.app import TranscribedPage
    from rmspec.cli._invoke import Invoked
    from rmspec.cli._output import CliOutput
    from rmspec.cli._settings import CliSettings
    from rmspec.domain.models import OcrProvenance, Palette, ScreenSpec
    from rmspec.domain.ports.render import RenderStyle

__all__ = ["ocr"]

_COMMAND: Final = "ocr"
"""The invocation this command answers, and its key in :data:`RESPONSE_TYPES`."""

_CONTAINER_MODULE: Final = "rmspec.cli._container"
"""Where the raster template lives, spelled as a string for the reason ``_invoke`` gives.

A static import would put every adapter this package must not name into
``rmspec --help``'s import graph, and the entry test's AST walk would fail the build.
"""

_TEMPLATE_ATTRIBUTE: Final = "RasterTemplate"
"""The container value carrying the screen, the palette and the style as one triple."""

_ENGINE_FEATURES: Final = {
    OcrEngineName.TEXTRACT: FEATURE_OCR_TEXTRACT,
    OcrEngineName.APPLE_VISION: FEATURE_OCR_APPLE_VISION,
}
"""Which optional module each selectable engine needs, so the probe covers only those.

Keyed by the enum rather than by its string value, so an engine added to
``RMSPEC_OCR_ENGINES`` without a feature here is a ``KeyError`` at the boundary instead of
a recogniser whose absent backend is discovered after the render was paid for.
"""

_MIN_THRESHOLD: Final = 0.0
"""Lowest agreement a caller may demand: everything agrees with everything."""

_MAX_THRESHOLD: Final = 1.0
"""Highest agreement a caller may demand: the two readings must be identical."""

_CONFIDENCE_PLACES: Final = 3
"""Decimal places a mean confidence is rendered to in a ``DENSE`` cell."""

_FAILURE_SEPARATOR: Final = ";"
"""What separates one recogniser failure from the next inside one ``DENSE`` cell."""

_FAILURE_ASSIGN: Final = "="
"""What joins a failing recogniser's ``provider_id`` to why it failed."""

_ABSENT: Final = ""
"""How ``None`` is spelled in a ``DENSE`` cell, since the format has no null."""

_FLAG_TEXT: Final = {True: "true", False: "false"}
"""How a boolean is spelled in a ``DENSE`` cell: the JSON spelling, not Python's.

A mapping rather than a conditional, because ``str(True)`` is ``"True"`` and a consumer
grepping a column for ``true`` would silently match nothing.
"""

_OCR_COLUMNS: Final = (
    "page_index",
    "tier_reached",
    "short_circuited",
    "cached",
    "cached_tier",
    "truncated",
    "mean_confidence",
    "recognizer_failures",
    "text",
)
"""The ``DENSE`` and ``HUMAN`` projection: the page, what it cost, and what it says.

``rmspec ocr`` on a 432-page document is megabytes of JSON and a few hundred kilobytes of
this, which is the whole reason ``DENSE`` exists. ``text`` is last so ``cut -f9-`` gets it
whole, and every cost field a caller might budget against comes before it. ``cached_tier``
is the cached row's own ``tier_reached``, which is the one thing this run's
``tier_reached`` cannot say.
"""

_HUMAN_FIELDS: Final = (0, 1, 2, 3, 8)
"""Which of :data:`_OCR_COLUMNS` a person's table shows, as positions in the same record.

Nine columns in an 80-column terminal is nine ellipses: ``rich`` truncates every cell and
the table says nothing. So ``HUMAN`` projects a narrow read of the *same* row -- the page,
how far up the ladder it went, whether it short-circuited, whether it was cached, and the
text -- while ``--json`` and ``--dense`` carry every field. Positions rather than a second
row builder, so the two views cannot disagree about a value.
"""

_HUMAN_COLUMNS: Final = ("page", "tier", "short", "cached", "text")
"""Headings for :data:`_HUMAN_FIELDS`, shortened so ``rich`` does not truncate them.

A person reading a table does not need ``short_circuited`` spelled out to know what the
column is; a machine reading ``--dense`` does, and gets it.
"""

ThresholdOption = Annotated[float | None, Parameter(name="--threshold")]
"""``--threshold``: tier-0/tier-1 agreement, overriding ``RMSPEC_AGREEMENT_THRESHOLD``."""


def _features(settings: CliSettings, /) -> tuple[str, ...]:
    """Give every optional module this composition will actually use.

    Parameters
    ----------
    settings
        Supplies :attr:`~rmspec.cli._settings.CliSettings.ocr_engines`.

    Returns
    -------
    tuple[str, ...]
        Scene decoding, rasterizing, one feature per selected engine in declaration order,
        and the Bedrock feature for tiers 2 and 3.
    """
    engines = tuple(
        _ENGINE_FEATURES[name] for name in OcrEngineName if name in settings.ocr_engines
    )
    return (FEATURE_SCENE_DECODE, FEATURE_RASTER, *engines, FEATURE_MODEL_BEDROCK)


def _template(invoked: Invoked, /) -> tuple[ScreenSpec, Palette, RenderStyle]:
    """Resolve the container's screen, palette and style, which it holds as one triple.

    Parameters
    ----------
    invoked
        The open invocation.

    Returns
    -------
    tuple[~rmspec.domain.models.ScreenSpec, ~rmspec.domain.models.Palette,\
 ~rmspec.domain.ports.render.RenderStyle]
        The three render inputs that are constant for a run. Taken from the container
        rather than named here, so "the export palette, not the on-screen one" is decided
        in one place -- and so this command and ``rmspec render`` cannot rasterize
        differently shaped pages for one document.
    """
    module = importlib.import_module(_CONTAINER_MODULE)
    template = invoked.get(getattr(module, _TEMPLATE_ATTRIBUTE))
    return template.screen, template.palette, template.style


def _threshold(settings: CliSettings, /, *, override: float | None) -> float:
    """Give the agreement this run holds tier 0 and a tier-1 reading to.

    Parameters
    ----------
    settings
        Supplies :attr:`~rmspec.cli._settings.CliSettings.agreement_threshold`, 0.90 --
        the design's measured starting point, and a setting at all because no single
        threshold is right for every hand.
    override
        The ``--threshold`` value, or ``None`` to take the setting.

    Returns
    -------
    float
        The threshold, for the request's ``agreement_threshold``.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *override* is outside ``0.0``--``1.0``, which is a similarity no measurement can
        meet or miss. Refused here rather than by the request's own bounds, because a
        ``pydantic.ValidationError`` escaping a command body is a second error vocabulary
        with a traceback and exit status 1.
    """
    if override is None:
        return settings.agreement_threshold
    if not _MIN_THRESHOLD <= override <= _MAX_THRESHOLD:
        raise UsageError(
            subject=f"--threshold {override}",
            requirement=f"a similarity between {_MIN_THRESHOLD} and {_MAX_THRESHOLD}",
        )
    return override


def _cached_tier(provenance: OcrProvenance | None, /) -> str:
    """Say which tier produced a cached row, or nothing when the row is not cached.

    Parameters
    ----------
    provenance
        The served row's own account of itself, which is ``None`` exactly when this page
        was not a cache hit.

    Returns
    -------
    str
        The row's ``tier_reached``, or the empty cell. Distinct from this run's
        ``tier_reached``, which is ``0`` for every hit because it describes what this run
        paid rather than what the row cost when it was made.
    """
    if provenance is None:
        return _ABSENT
    return str(provenance.tier_reached)


def _confidence(value: float | None, /) -> str:
    """Render a mean recogniser confidence, or nothing when no engine reported one.

    Parameters
    ----------
    value
        The mean, or ``None``.

    Returns
    -------
    str
        The value to three places, or the empty cell -- never a fabricated ``0.0`` that
        reads as a garbage reading or a ``1.0`` that reads as a perfect one.
    """
    if value is None:
        return _ABSENT
    return f"{value:.{_CONFIDENCE_PLACES}f}"


def _failures(failures: Mapping[str, str], /) -> str:
    """Flatten the per-engine failure map into one cell.

    Parameters
    ----------
    failures
        Why each tier-1 engine that failed did so, keyed by its ``provider_id``. Empty on
        the happy path and on a cache hit.

    Returns
    -------
    str
        ``engine=reason`` pairs joined by a semicolon, sorted by engine so two runs of one
        degraded command produce the same cell, or the empty cell when every engine
        answered.
    """
    return _FAILURE_SEPARATOR.join(
        f"{provider}{_FAILURE_ASSIGN}{reason}" for provider, reason in sorted(failures.items())
    )


def _rows(pages: Sequence[TranscribedPage], /) -> Iterator[tuple[str, ...]]:
    """Project the transcriptions onto :data:`_OCR_COLUMNS`.

    Parameters
    ----------
    pages
        The transcribed pages, in ascending document order.

    Yields
    ------
    tuple[str, ...]
        One already-stringified record per page.
    """
    for page in pages:
        yield (
            str(page.page.page_index),
            str(page.tier_reached),
            _FLAG_TEXT[page.short_circuited],
            _FLAG_TEXT[page.cached],
            _cached_tier(page.cached_provenance),
            _FLAG_TEXT[page.truncated],
            _confidence(page.mean_confidence),
            _failures(page.recognizer_failures),
            page.page.text,
        )


def _present(out: CliOutput, pages: Sequence[TranscribedPage], /) -> None:
    """Show the transcriptions on the stream this invocation's mode requires.

    Parameters
    ----------
    out
        The invocation's writer.
    pages
        The transcribed pages.

    Notes
    -----
    ``DENSE`` writes tab-separated records to **stdout**; ``HUMAN`` writes a table to
    **stderr**. That split is the frozen output contract, and it is what makes
    ``rmspec ocr doc --json | jq`` clean by construction.
    """
    if out.mode is OutputMode.DENSE:
        out.rows(_OCR_COLUMNS, _rows(pages))
        return
    table = Table(*_HUMAN_COLUMNS)
    for row in _rows(pages):
        table.add_row(*(row[field] for field in _HUMAN_FIELDS))
    out.display(table)


def ocr(
    doc: str,
    /,
    *,
    pages: PagesOption = None,
    limit: LimitOption = None,
    max_pages: MaxPagesOption = None,
    strict: StrictFlag = False,
    threshold: ThresholdOption = None,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Transcribe handwriting on pages of one document, paying only for the tiers needed.

    Parameters
    ----------
    doc
        Which document: a name substring, a full uuid, or a uuid prefix.
    pages
        Which pages to transcribe: a comma-separated list of 0-based page indices and
        inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Mutually exclusive with
        ``--limit``.
    limit
        Transcribe at most this many leading pages. Mutually exclusive with ``--pages``.
    max_pages
        Override ``RMSPEC_MAX_PAGES`` for this run only. The cap is enforced before any
        render, raster or model call, so one 432-page document cannot silently become 432
        model calls.
    strict
        Refuse an ambiguous selector instead of accepting the ranked winner and reporting
        the substitution.
    threshold
        Agreement at or above which the tablet's own reading and a recogniser's are held
        to agree, which skips tiers 2 and 3. Overrides ``RMSPEC_AGREEMENT_THRESHOLD``
        (0.90); one threshold is not right for every hand, which is why it is a setting.
    json
        Emit one envelope on stdout, whose ``data`` is the whole transcription result.
        Mutually exclusive with ``--dense``.
    dense
        Emit tab-separated records on stdout, one per page: the page, what the run paid for
        it, and its text. Mutually exclusive with ``--json``.

    Returns
    -------
    int
        ``0``.
    """

    def body(invoked: Invoked) -> int:
        invoked.probe(*_features(invoked.settings))
        agreement = _threshold(invoked.settings, override=threshold)
        screen, palette, style = _template(invoked)
        resolved = invoked.document(doc, strict=strict)
        result = invoked.get(TranscribePages).transcribe(
            TranscribePagesRequest(
                render=RenderPagesRequest(
                    document_uuid=resolved.chosen.uuid,
                    selection=invoked.selection(pages=pages, limit=limit),
                    max_pages=invoked.max_pages(max_pages),
                    screen=screen,
                    palette=palette,
                    style=style,
                    raster_dpi=invoked.settings.ocr_dpi,
                ),
                now=datetime.now(tz=UTC),
                agreement_threshold=agreement,
            )
        )
        degradations = (*resolved.degradations, *result.degradations)
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                result.model_dump(mode="json"),
                response_type=RESPONSE_TYPES[_COMMAND],
                degradations=degradations,
            )
        else:
            invoked.report(degradations)
            _present(invoked.out, result.pages)
        return 0

    return run(body, json=json, dense=dense)
