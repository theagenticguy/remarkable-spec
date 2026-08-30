"""``rmspec render``: turn selected pages into SVG, PNG or PDF files on this host.

One use case, three artifact kinds
----------------------------------
:class:`~rmspec.app.RenderPages` decodes, renders and -- when asked -- rasterizes. This
module adds only the part that is genuinely the edge's: choosing which of the three
artifact kinds the run commits, and committing them. ``svg`` is the markup the renderer
already produced, ``png`` is the pixels the same call rasterized, and ``pdf`` is those
pages composed through :class:`~rmspec.domain.ports.export.PdfComposer`. Nothing here
re-renders, so the three formats cannot disagree about what a page looks like.

``--dpi`` and ``--thickness`` are read, not decorated with
---------------------------------------------------------
This is the named legacy defect the rename exists to fix: the old tree accepted both
flags and three of its four raster commands hardcoded 300 DPI anyway, while the declared
``RMSPEC_DPI`` said 226 and nothing read it. So there is no resolution and no thickness
literal in this file. ``--dpi`` defaults to
:attr:`~rmspec.cli._settings.CliSettings.render_dpi` -- 229, the Paper Pro panel's own
density, so a default render is 1:1. That setting read 226 until 2026-08-30, which is the
reMarkable 2's density and not this tablet's: a zero-reader legacy value carries a
borrowed justification, and a render 1.31% under 1:1 is what it cost. ``--thickness``
defaults to :attr:`~rmspec.cli._settings.CliSettings.thickness`, reached through the
:class:`~rmspec.domain.ports.render.RenderStyle` the container already built from it.
``RMSPEC_OCR_DPI`` is a different quantity for a different job and is never read here;
conflating the two is the defect the settings split fixed.

``--dpi`` with ``--format svg`` is a :class:`~rmspec.domain.errors.UsageError` rather
than a value that is quietly dropped. SVG is vector markup and carries no resolution, and
accepting a flag that changes nothing is how the legacy tree taught callers that ``--dpi``
worked.

Why this module writes files itself, and what should replace it
--------------------------------------------------------------
:class:`~rmspec.domain.ports.export.ArtifactSink` is the port for committing bytes, and
``rmspec-export`` ships a filesystem adapter for it that is atomic, has an overwrite
policy and has a dry run. **The container binds no sink**, because a sink is constructed
with the invocation's destination and the destination is a positional argument of this
command -- so it cannot be an ``APP``-scoped provider, and nothing in the request scope's
context carries an output path today. This module may not name an adapter
(``tests/test_cli_entry.py`` AST-walks every ``cli/*.py`` but ``_container.py``), so the
write happens here, in :func:`_commit`, against the domain's own vocabulary:
:class:`~rmspec.domain.ports.export.ArtifactName` refuses traversal before any path is
built, :class:`~rmspec.domain.ports.export.ArtifactRef` is the receipt, and every refusal
is an :class:`~rmspec.domain.errors.ArtifactWriteFailed` carrying one of the closed
:class:`~rmspec.domain.errors.ArtifactWriteReason` members.

The replacement is a request-scoped sink **factory**: give the container a way to build a
sink from a per-invocation destination, overwrite flag and dry-run flag, and this module
loses :func:`_commit`, :func:`_atomic_write` and :func:`_reason` and calls
``invoked.get(ArtifactSink).write(...)`` instead. Its output does not change, because the
receipt it renders is already the port's.

``OUT`` is a directory
----------------------
Every format writes into it, and it is created when it does not exist. One rule with no
branch, and it is the shape the sink takes as well -- its destination is a directory and
it derives each suffix from :class:`~rmspec.domain.ports.export.ArtifactMedia`, so a name
and its content cannot disagree. Per-page artifacts are named ``page-NNNN`` with the
**0-based** ``page_index`` the JSON payload reports, and the single PDF is named for the
document's uuid. A caller never has to guess: every receipt carries the ``uri`` the bytes
landed at.
"""

from __future__ import annotations

import errno
import importlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, Literal

from cyclopts import Parameter
from rich.table import Table

from rmspec.app import RenderPages, RenderPagesRequest
from rmspec.cli._invoke import (
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
from rmspec.domain.errors import (
    ArtifactWriteFailed,
    ArtifactWriteReason,
    RasterizationFailed,
    UsageError,
)
from rmspec.domain.ports.export import (
    ArtifactMedia,
    ArtifactName,
    ArtifactRef,
    PdfComposer,
    PhysicalSize,
    SvgPage,
    SvgPageSet,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from rmspec.app import RenderedPageArtifact, RenderPagesResult
    from rmspec.cli._invoke import Invoked
    from rmspec.cli._output import CliOutput
    from rmspec.cli._settings import CliSettings
    from rmspec.domain.models import Palette, ScreenSpec
    from rmspec.domain.ports.render import RenderStyle

__all__ = ["render"]

_COMMAND: Final = "render"
"""The invocation this command answers, and its key in :data:`RESPONSE_TYPES`."""

_CONTAINER_MODULE: Final = "rmspec.cli._container"
"""Where the raster template lives, spelled as a string for the reason ``_invoke`` gives.

A static ``from rmspec.cli._container import RasterTemplate`` would put every adapter this
package must not name into ``rmspec --help``'s import graph, and the entry test's AST walk
would fail the build for it.
"""

_TEMPLATE_ATTRIBUTE: Final = "RasterTemplate"
"""The container value that carries the screen, the palette and the style as one triple.

Named once so a rename in the container fails one test here rather than turning a render
into a differently-shaped page. The template also carries ``raster_dpi``, which this
command deliberately does **not** read: that field is recognition density, and
``rmspec render``'s density is :attr:`~rmspec.cli._settings.CliSettings.render_dpi`.
"""

_ARTIFACTS_KEY: Final = "artifacts"
"""Where the receipts go inside the envelope's ``data``.

``data`` is :class:`~rmspec.app.RenderPagesResult` plus this one key. A superset, and
deliberately: the whole point of this command is that files landed, and a machine-readable
answer that made a caller guess the paths would push the naming convention into every
consumer.
"""

_WITHOUT_PIXELS: Final = {"pages": {"__all__": {"raster": {"data"}}}}
"""The one thing dropped from the envelope's ``data``: each page's encoded pixels.

Not a preference. :attr:`~rmspec.domain.ports.export.RasterImage.data` is ``bytes`` and
pydantic's JSON mode decodes bytes as UTF-8, so dumping a PNG raises
``UnicodeDecodeError`` -- the envelope is unserialisable otherwise. Everything that
*describes* the pixels stays (encoding, both dimensions, the density they were rendered
at), and the pixels themselves are in the file whose ``uri`` the receipt names, which is
where a caller wanted them. Base64 in the envelope was the alternative and it would put a
megabyte per page into a document whose whole job is to be read.
"""

_PAGE_STEM: Final = "page-"
"""Prefix of a per-page artifact name, before its zero-padded 0-based index."""

_INDEX_WIDTH: Final = 4
"""Digits a page index is padded to, so ``ls`` sorts a 432-page export correctly."""

_SCRATCH_PREFIX: Final = ".rmspec-"
"""Prefix of the temporary file a commit renames onto its target."""

_FLAG_TEXT: Final = {True: "true", False: "false"}
"""How a boolean is spelled in a ``DENSE`` cell: the JSON spelling, not Python's.

A mapping rather than a conditional, because ``str(True)`` is ``"True"`` and a consumer
grepping a column for ``true`` would silently match nothing -- and because a lookup has no
branch for a coverage run to leave half-taken.
"""

_SVG_FORMAT: Final = ArtifactMedia.SVG.value
"""The one format that needs no native raster library and takes no resolution."""

_RENDER_COLUMNS: Final = ("name", "media", "uri", "byte_count", "committed")
"""The ``DENSE`` and ``HUMAN`` projection: one record per artifact, not per page.

The identity plus what the command is *for*. ``rmspec render`` exists to put files
somewhere, so a bounded-context caller wants the name, the kind, where it landed, how
big it is, and whether it really landed or was only predicted. Page identity,
``page_hash`` and the render digest are in ``--json``, which is the contract.
"""

_WRITE_REASONS: Final = {
    errno.ENOSPC: ArtifactWriteReason.OUT_OF_SPACE,
    errno.EDQUOT: ArtifactWriteReason.OUT_OF_SPACE,
    errno.EFBIG: ArtifactWriteReason.OUT_OF_SPACE,
    errno.EACCES: ArtifactWriteReason.NOT_WRITABLE,
    errno.EPERM: ArtifactWriteReason.NOT_WRITABLE,
    errno.EROFS: ArtifactWriteReason.NOT_WRITABLE,
    errno.EISDIR: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENOTDIR: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENOENT: ArtifactWriteReason.NOT_WRITABLE,
    errno.ENAMETOOLONG: ArtifactWriteReason.NOT_WRITABLE,
}
"""``errno`` to the closed reason the domain names, so the tree grows no class per code.

Everything unlisted is :attr:`~rmspec.domain.errors.ArtifactWriteReason.INTERRUPTED`,
which is the honest reading of "the write started and did not finish" for a code nobody
anticipated -- and :func:`_atomic_write` has already removed the temporary file, which is
the guarantee that member carries.
"""

FormatOption = Annotated[Literal["svg", "png", "pdf"], Parameter(name="--format")]
"""``--format``: which artifact to commit. A ``Literal`` so ``rmspec manifest`` lists the
closed set of choices and a typo is refused by the parser rather than by a suffix table."""

DpiOption = Annotated[int | None, Parameter(name="--dpi")]
"""``--dpi``: raster density, overriding ``RMSPEC_RENDER_DPI`` for this run only."""

ThicknessOption = Annotated[float | None, Parameter(name="--thickness")]
"""``--thickness``: stroke-width multiplier, overriding ``RMSPEC_THICKNESS``."""

OverwriteFlag = Annotated[bool, Parameter(name="--overwrite", negative="")]
"""``--overwrite``: replace an artifact that is already there instead of refusing."""

DryRunFlag = Annotated[bool, Parameter(name="--dry-run", negative="")]
"""``--dry-run``: report where the bytes would go and how many there are, and write none."""


def _features(fmt: str, /) -> tuple[str, ...]:
    """Give the optional modules this run will actually use.

    Parameters
    ----------
    fmt
        The ``--format`` value.

    Returns
    -------
    tuple[str, ...]
        Scene decoding always, plus the raster feature for anything that reaches a native
        drawing library -- which is PNG, and PDF, because SVG-to-PDF composition goes
        through the same ``cairo`` bindings.
    """
    if fmt == _SVG_FORMAT:
        return (FEATURE_SCENE_DECODE,)
    return (FEATURE_SCENE_DECODE, FEATURE_RASTER)


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
        The three render inputs that are constant for a run, taken from the container
        rather than named here: "the export palette, not the on-screen one" and "the Paper
        Pro panel" are composition decisions, and a command that restated them would be a
        second place for them to drift -- the same class of defect as a hardcoded DPI. The
        template's own ``raster_dpi`` is deliberately not read; see
        :data:`_TEMPLATE_ATTRIBUTE`.
    """
    module = importlib.import_module(_CONTAINER_MODULE)
    template = invoked.get(getattr(module, _TEMPLATE_ATTRIBUTE))
    return template.screen, template.palette, template.style


def _raster_dpi(settings: CliSettings, /, *, dpi: int | None, media: ArtifactMedia) -> int | None:
    """Decide the resolution this run rasterizes at, or that it does not rasterize.

    Parameters
    ----------
    settings
        Supplies :attr:`~rmspec.cli._settings.CliSettings.render_dpi`, which is 229 -- the
        Paper Pro panel's own density, so the default render is 1:1.
    dpi
        The ``--dpi`` value, or ``None`` to take the setting.
    media
        Which artifact this run commits.

    Returns
    -------
    int | None
        The density, or ``None`` for SVG -- which is the whole of "do not rasterize", and
        is what keeps an SVG export to one backend call per page instead of two.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        ``--dpi`` was passed for an SVG export, which carries no resolution, or it is not
        positive. Both are refused here rather than left to
        :class:`~rmspec.app.RenderPagesRequest`'s ``gt=0``, because a
        ``pydantic.ValidationError`` escaping a command body is a second error vocabulary
        with a traceback and exit status 1.
    """
    if media is ArtifactMedia.SVG:
        if dpi is not None:
            raise UsageError(
                subject=f"--dpi {dpi} with --format {_SVG_FORMAT}",
                requirement="a raster format, because SVG is vector markup and carries "
                "no resolution",
            )
        return None
    if dpi is None:
        return settings.render_dpi
    if dpi <= 0:
        raise UsageError(subject=f"--dpi {dpi}", requirement="a resolution above zero")
    return dpi


def _style(base: RenderStyle, /, *, thickness: float | None) -> RenderStyle:
    """Apply ``--thickness`` to the style the container built from the setting.

    Parameters
    ----------
    base
        The container's style, already carrying ``RMSPEC_THICKNESS``, the minimum padding
        and the renderer revision a cached row is keyed on.
    thickness
        The ``--thickness`` value, or ``None`` to keep the setting.

    Returns
    -------
    ~rmspec.domain.ports.render.RenderStyle
        *base* unchanged, or a copy differing in one field. Copied rather than rebuilt
        field by field, so a field added to ``RenderStyle`` cannot be silently dropped
        here; the value is validated above instead of by the model, for the same reason
        :func:`_raster_dpi` validates its own.

    Raises
    ------
    ~rmspec.domain.errors.UsageError
        *thickness* is not positive. A zero multiplier is not a thinner stroke, it is a
        page with no ink that still reports every stroke it drew.
    """
    if thickness is None:
        return base
    if thickness <= 0:
        raise UsageError(
            subject=f"--thickness {thickness}",
            requirement="a stroke-width multiplier above zero",
        )
    return base.model_copy(update={"thickness_scale": thickness})


def _page_name(artifact: RenderedPageArtifact, /) -> ArtifactName:
    """Name one page's artifact after its 0-based index in the document.

    Parameters
    ----------
    artifact
        The rendered page.

    Returns
    -------
    ~rmspec.domain.ports.export.ArtifactName
        ``page-NNNN``, zero-padded. A stem, never a filename: the suffix comes from the
        media, so a name and its content cannot disagree.
    """
    return ArtifactName(value=f"{_PAGE_STEM}{artifact.page_index:0{_INDEX_WIDTH}d}")


def _pixels(artifact: RenderedPageArtifact, /) -> bytes:
    """Take one page's PNG bytes, refusing a page that came back without any.

    Parameters
    ----------
    artifact
        The rendered page.

    Returns
    -------
    bytes
        The encoded pixels.

    Raises
    ------
    ~rmspec.domain.errors.RasterizationFailed
        The page has no raster although a resolution was requested. Unreachable through
        :class:`~rmspec.app.RenderPages`, which rasterizes whenever ``raster_dpi`` is set,
        and reachable through any other binding of the same shape -- so it is a refusal
        rather than an ``assert`` or a silently empty file.
    """
    if artifact.raster is None:
        raise RasterizationFailed(
            backend="the render pipeline",
            detail="pixels were requested for this page and the pipeline returned none",
            page_ref=artifact.page_ref,
        )
    return artifact.raster.data


def _svg_page(artifact: RenderedPageArtifact, /) -> SvgPage:
    """Re-wrap one rendered page as the value the PDF composer accepts.

    Parameters
    ----------
    artifact
        The rendered page, and the sole source of all three fields below.

    Returns
    -------
    ~rmspec.domain.ports.export.SvgPage
        The same page in the export slice's vocabulary. The two ``PhysicalSize`` classes
        are field-for-field twins in two ports modules and pydantic models are nominal, so
        the re-wrap is forced; :mod:`rmspec.app.render` makes the same hop for the same
        reason, and the mitigation is the same -- one argument, every field derived from
        it, so no caller can attribute one page's markup to another.
    """
    return SvgPage(
        page_ref=artifact.page_ref,
        svg=artifact.rendered.svg,
        size=PhysicalSize(
            width_mm=artifact.rendered.size.width_mm,
            height_mm=artifact.rendered.size.height_mm,
        ),
    )


def _payloads(
    result: RenderPagesResult,
    /,
    *,
    invoked: Invoked,
    media: ArtifactMedia,
) -> tuple[tuple[ArtifactName, bytes], ...]:
    """Turn a render into the named byte strings this run commits.

    Parameters
    ----------
    result
        The render, whose pages are already in ascending document order.
    invoked
        The open invocation, for the PDF composer. Resolved only on the PDF path, so an
        SVG or PNG export never builds one.
    media
        Which artifact this run commits.

    Returns
    -------
    tuple[tuple[~rmspec.domain.ports.export.ArtifactName, bytes], ...]
        One entry per page for SVG and PNG, and exactly one for PDF. Empty when the
        document has no pages, which
        :meth:`~rmspec.app.PageSelection.resolve_against` returns for one -- naming no
        page is not an error, and returning early is also what keeps
        :class:`~rmspec.domain.ports.export.SvgPageSet`'s ``min_length=1`` from turning it
        into a ``ValidationError``.
    """
    if not result.pages:
        return ()
    if media is ArtifactMedia.PDF:
        composed = invoked.get(PdfComposer).compose(
            SvgPageSet(pages=tuple(_svg_page(page) for page in result.pages))
        )
        return ((ArtifactName(value=result.document_uuid), composed.data),)
    if media is ArtifactMedia.SVG:
        return tuple((_page_name(page), page.rendered.svg.encode()) for page in result.pages)
    return tuple((_page_name(page), _pixels(page)) for page in result.pages)


def _reason(error: OSError, /) -> ArtifactWriteReason:
    """Classify one filesystem failure as a reason the domain already names.

    Parameters
    ----------
    error
        The failure, whose ``errno`` is the only thing read: the message is the operating
        system's wording and travels through in ``detail`` instead.

    Returns
    -------
    ~rmspec.domain.errors.ArtifactWriteReason
        The member for this code, or
        :attr:`~rmspec.domain.errors.ArtifactWriteReason.INTERRUPTED` for one that is not
        in :data:`_WRITE_REASONS`.
    """
    return _WRITE_REASONS.get(error.errno or 0, ArtifactWriteReason.INTERRUPTED)


def _atomic_write(target: Path, payload: bytes, /) -> None:
    """Commit bytes whole or leave the target untouched.

    Parameters
    ----------
    target
        Where the bytes belong.
    payload
        The complete artifact.

    Raises
    ------
    OSError
        The temporary file could not be created, written, flushed or renamed. The
        temporary is removed first, so a failed 200-page export leaves neither a truncated
        artifact nor a scratch file behind -- which is the guarantee
        :class:`~rmspec.domain.errors.ArtifactWriteFailed` makes on the caller's behalf.
    """
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=_SCRATCH_PREFIX)
    scratch = Path(temporary)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        scratch.replace(target)
    except OSError:
        scratch.unlink(missing_ok=True)
        raise


def _destination(out: Path, /, *, dry_run: bool) -> Path:
    """Resolve the output directory, creating it unless this is a dry run.

    Parameters
    ----------
    out
        The ``OUT`` argument as the user typed it.
    dry_run
        Whether ``--dry-run`` was passed. A dry run touches the filesystem not at all, so
        it does not create the directory it would have written into.

    Returns
    -------
    ~pathlib.Path
        The absolute directory, so every receipt's ``uri`` is absolute.

    Raises
    ------
    ~rmspec.domain.errors.ArtifactWriteFailed
        The directory could not be created, with the reason
        :func:`_reason` gives its ``errno``.
    """
    resolved = out.expanduser().resolve()
    if dry_run:
        return resolved
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ArtifactWriteFailed(
            name=str(resolved),
            reason=_reason(error),
            detail=f"the output directory could not be created: {error}",
        ) from error
    return resolved


def _commit(
    payload: bytes,
    /,
    *,
    name: ArtifactName,
    media: ArtifactMedia,
    destination: Path,
    overwrite: bool,
    dry_run: bool,
) -> ArtifactRef:
    """Write one artifact, or say where it would have gone.

    Parameters
    ----------
    payload
        The complete artifact.
    name
        The stem, already refused by
        :class:`~rmspec.domain.ports.export.ArtifactName` if it addressed anything but one
        file in *destination*.
    media
        What kind of artifact this is, and the sole source of the suffix.
    destination
        The directory, already created unless this is a dry run.
    overwrite
        Whether ``--overwrite`` was passed.
    dry_run
        Whether ``--dry-run`` was passed.

    Returns
    -------
    ~rmspec.domain.ports.export.ArtifactRef
        The receipt, with ``committed`` false on a dry run -- so the report phrases itself
        from what the writer says happened rather than by reading a flag back to itself.

    Raises
    ------
    ~rmspec.domain.errors.ArtifactWriteFailed
        The target is already there and ``--overwrite`` was not passed, or the write
        failed. Overwriting a file the caller did not ask to overwrite is not an option:
        an export is the one command whose whole effect is on someone else's disk.
    """
    target = destination / f"{name.value}.{media.value}"
    if dry_run:
        return ArtifactRef(
            name=name,
            uri=target.as_uri(),
            byte_count=len(payload),
            media=media,
            committed=False,
        )
    if target.exists() and not overwrite:
        raise ArtifactWriteFailed(
            name=name.value,
            reason=ArtifactWriteReason.ALREADY_PRESENT,
            detail=f"{target} already holds an artifact; pass --overwrite to replace it",
        )
    try:
        _atomic_write(target, payload)
    except OSError as error:
        raise ArtifactWriteFailed(
            name=name.value,
            reason=_reason(error),
            detail=str(error),
        ) from error
    return ArtifactRef(
        name=name,
        uri=target.as_uri(),
        byte_count=len(payload),
        media=media,
        committed=True,
    )


def _write_all(
    result: RenderPagesResult,
    /,
    *,
    invoked: Invoked,
    media: ArtifactMedia,
    out: Path,
    overwrite: bool,
    dry_run: bool,
) -> tuple[ArtifactRef, ...]:
    """Commit every artifact this render produced.

    Parameters
    ----------
    result
        The render.
    invoked
        The open invocation.
    media
        Which artifact kind to commit.
    out
        The ``OUT`` directory.
    overwrite
        Whether ``--overwrite`` was passed.
    dry_run
        Whether ``--dry-run`` was passed.

    Returns
    -------
    tuple[~rmspec.domain.ports.export.ArtifactRef, ...]
        One receipt per artifact, in page order. Empty for a document with no pages, and
        no directory is created for one either.
    """
    payloads = _payloads(result, invoked=invoked, media=media)
    if not payloads:
        return ()
    destination = _destination(out, dry_run=dry_run)
    return tuple(
        _commit(
            payload,
            name=name,
            media=media,
            destination=destination,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        for name, payload in payloads
    )


def _rows(refs: Sequence[ArtifactRef], /) -> Iterator[tuple[str, ...]]:
    """Project the receipts onto :data:`_RENDER_COLUMNS`.

    Parameters
    ----------
    refs
        The receipts.

    Yields
    ------
    tuple[str, ...]
        One already-stringified record per artifact.
    """
    for ref in refs:
        yield (
            ref.name.value,
            ref.media.value,
            ref.uri,
            str(ref.byte_count),
            _FLAG_TEXT[ref.committed],
        )


def _present(out: CliOutput, refs: Sequence[ArtifactRef], /) -> None:
    """Show what landed, on the stream this invocation's mode requires.

    Parameters
    ----------
    out
        The invocation's writer.
    refs
        The receipts.

    Notes
    -----
    ``DENSE`` writes tab-separated records to **stdout**; ``HUMAN`` writes a table to
    **stderr**. That split is the frozen output contract and it is what makes
    ``rmspec render doc out --json | jq`` clean by construction and
    ``rmspec render doc out 2>/dev/null`` correctly silent.
    """
    if out.mode is OutputMode.DENSE:
        out.rows(_RENDER_COLUMNS, _rows(refs))
        return
    table = Table(*_RENDER_COLUMNS)
    for row in _rows(refs):
        table.add_row(*row)
    out.display(table)


def render(
    doc: str,
    out: Path,
    /,
    *,
    pages: PagesOption = None,
    limit: LimitOption = None,
    max_pages: MaxPagesOption = None,
    strict: StrictFlag = False,
    fmt: FormatOption = "svg",
    dpi: DpiOption = None,
    thickness: ThicknessOption = None,
    overwrite: OverwriteFlag = False,
    dry_run: DryRunFlag = False,
    json: JsonFlag = False,
    dense: DenseFlag = False,
) -> int:
    """Render pages of one document into SVG, PNG or PDF files in a directory.

    Parameters
    ----------
    doc
        Which document: a name substring, a full uuid, or a uuid prefix.
    out
        The directory the artifacts are written into, created if it does not exist. Every
        format writes here; per-page artifacts are ``page-NNNN`` with the 0-based page
        index, and a PDF is named for the document's uuid.
    pages
        Which pages to render: a comma-separated list of 0-based page indices and
        inclusive A-B ranges, as in 0 or 2-5 or 0,3,7-9. Mutually exclusive with
        ``--limit``.
    limit
        Render at most this many leading pages. Mutually exclusive with ``--pages``.
    max_pages
        Override ``RMSPEC_MAX_PAGES`` for this run only. The cap is enforced before any
        page is decoded, so one 432-page document cannot silently become 432 renders.
    strict
        Refuse an ambiguous selector instead of accepting the ranked winner and reporting
        the substitution.
    fmt
        Which artifact to commit: ``svg`` for the markup, ``png`` for pixels, ``pdf`` for
        every selected page composed into one document.
    dpi
        Raster density, overriding ``RMSPEC_RENDER_DPI`` (229, the Paper Pro panel's own
        density, so the default is a 1:1 render). Refused with ``--format svg``, which
        carries no resolution.
    thickness
        Stroke-width multiplier, overriding ``RMSPEC_THICKNESS`` (1.5). It reaches all
        three formats, because it changes the markup every one of them is derived from.
    overwrite
        Replace an artifact that is already present instead of refusing it.
    dry_run
        Report where the bytes would land and how many there are, and write none.
    json
        Emit one envelope on stdout. Its ``data`` is the render result plus ``artifacts``,
        the receipts. Mutually exclusive with ``--dense``.
    dense
        Emit tab-separated records on stdout, one per artifact. Mutually exclusive with
        ``--json``.

    Returns
    -------
    int
        ``0``.
    """

    def body(invoked: Invoked) -> int:
        invoked.probe(*_features(fmt))
        media = ArtifactMedia(fmt)
        raster_dpi = _raster_dpi(invoked.settings, dpi=dpi, media=media)
        screen, palette, base = _template(invoked)
        style = _style(base, thickness=thickness)
        resolved = invoked.document(doc, strict=strict)
        result = invoked.get(RenderPages).render(
            RenderPagesRequest(
                document_uuid=resolved.chosen.uuid,
                selection=invoked.selection(pages=pages, limit=limit),
                max_pages=invoked.max_pages(max_pages),
                screen=screen,
                palette=palette,
                style=style,
                raster_dpi=raster_dpi,
            )
        )
        refs = _write_all(
            result,
            invoked=invoked,
            media=media,
            out=out,
            overwrite=overwrite,
            dry_run=dry_run,
        )
        degradations = (*resolved.degradations, *result.degradations)
        if invoked.out.mode is OutputMode.JSON:
            invoked.out.emit(
                {
                    **result.model_dump(mode="json", exclude=_WITHOUT_PIXELS),
                    _ARTIFACTS_KEY: [ref.model_dump(mode="json") for ref in refs],
                },
                response_type=RESPONSE_TYPES[_COMMAND],
                degradations=degradations,
            )
        else:
            invoked.report(degradations)
            _present(invoked.out, refs)
        return 0

    return run(body, json=json, dense=dense)
