"""Process-wide configuration for the CLI, and the one environment mutation it performs.

Five corrections to the legacy settings model live here, each measured rather than guessed.

``extra="forbid"``, and the half of it that ``extra="forbid"`` does not do
------------------------------------------------------------------------
Legacy discarded a mistyped ``RMSPEC_*`` silently, so a user who exported
``RMSPEC_XOCHITLE`` got the default xochitl root and no warning -- a misconfiguration that
presents as "the tool cannot see my documents". Flipping ``extra`` to ``forbid`` is the
documented fix and it is **not sufficient**, which was measured on pydantic-settings
2.13.1: ``EnvSettingsSource`` iterates the *declared fields* and asks the environment for
each, so a prefixed name no field claims is never offered to the model and ``forbid`` never
sees it. Only the dotenv source scans the other way. So ``forbid`` is set *and*
:class:`_UnknownPrefixedEnvVars` runs ahead of the standard source with the only job of
handing those orphans to the model, which is what turns them into a validation error.

``RMSPEC_DPI`` was two settings wearing one name
------------------------------------------------
Legacy declared ``RMSPEC_DPI = 226`` with **zero readers**, while three of its four raster
commands hardcoded ``300``. Those are different quantities -- one is a screen-equivalent
render, the other an OCR-quality raster -- and wiring the declared setting through would
have silently downgraded every OCR raster, so it is split into
:attr:`CliSettings.render_dpi` (229) and :attr:`CliSettings.ocr_dpi` (300). Both are now
live. ``RMSPEC_THICKNESS`` had zero readers too and wires straight through at ``1.5``.

``RMSPEC_RENDER_DPI`` was measured on the wrong device, and the split is what showed it
---------------------------------------------------------------------------------------
226 is ``RM2_SCREEN.dpi``, the reMarkable 1 and 2 panel, and it reached this file with the
legacy setting it was declared in. A value with zero readers is exactly where a borrowed
justification survives unchallenged -- nothing rendered at it, so nobody checked it against
the tablet this CLI is for -- which is why splitting one name into two is what caught it:
each number now has to be justified by what it is *for*, and this one could not be.

The domain refutes 226 in one line. ``PAPER_PRO_SCREEN`` is 1620x2160 at **229**, which the
panel's own geometry gives back: ``sqrt(1620**2 + 2160**2)`` is 2700 pixels across an
11.8-inch diagonal, and ``2700 / 11.8`` is 228.8. At 226 a Paper Pro page came out
1599x2132 rather than 1620x2160 -- 1.31% under the 1:1 render this file already promised.
:attr:`CliSettings.render_dpi` is **229** as of 2026-08-30, and the correction is five
sentences and one number: here, three times in ``_render.py`` -- one of them ``--dpi``'s
help text, which the manifest renders into ``AGENTS.md`` -- and once in ``_ocr.py``.

What it cost was measured rather than predicted: four tests, and not one recorded render
digest. ``render_dpi`` has exactly one reader, a provider in ``_render.py``, and SVG
carries no resolution, so every pinned SVG digest in ``rmspec-render``'s and
``rmspec-export``'s differential suites was unmoved. The four were the two default
assertions in ``test_cli_settings.py``, the ``RMSPEC_RENDER_DPI`` value
``test_cli_entry.py`` reads back out of ``rmspec env --json``, and the ``AGENTS.md`` drift
check, which ``mise run agents-md`` settles.
:attr:`CliSettings.ocr_dpi` did **not** move: 300 is the density the recognisers were tuned
against and is not a measurement of any panel, which is the whole point of two names.

``RMSPEC_SSH_KEY`` is new, and its default is the whole point
-------------------------------------------------------------
Measured live against firmware 3.27.3.0: ``ParamikoShell(key_path=None)`` raises
``DeviceAuthFailed`` against a tablet that ``ssh remarkable`` reaches from the same shell,
because **paramiko does not read** ``~/.ssh/config`` and the key is an ``IdentityFile`` line
in it. A ``None`` default therefore fails every SSH command on a correctly configured
machine, so the default is ``~/.ssh/id_ed25519_remarkable`` -- the path the legacy CLI
already probed by hand.

``RMSPEC_DEVICE_PASSWORD`` is dropped
-------------------------------------
``ParamikoShell`` has no password authentication path at all, by design. A setting nothing
can consume is a promise the code cannot keep, and the legacy tree kept it advertised in
``--help`` anyway.

The ``DYLD_FALLBACK_LIBRARY_PATH`` mutation is a step, not an import side effect
-------------------------------------------------------------------------------
Legacy assigned ``os.environ["DYLD_FALLBACK_LIBRARY_PATH"]`` while the CLI package was
being imported, so importing anything under ``rmspec.cli`` -- a test collecting a module, a
shell completion, ``--version`` -- mutated the process environment. It is
:func:`apply_native_library_path` here: named, called from exactly one place, taking the
mapping and the platform as arguments so a test can exercise both branches without touching
the interpreter it runs in.

Why failures leave here as :class:`~rmspec.domain.errors.InvalidSettingError`
----------------------------------------------------------------------------
The CLI's error envelope and its exit status both key on
:class:`~rmspec.domain.errors.RmspecError`, and ``InvalidSettingError`` is the row in the
domain's table that means "the environment is wrong, not the request" -- ``EX_CONFIG``, 78.
A ``pydantic.ValidationError`` escaping the composition root would be a traceback with exit
status 1, which is the legacy behaviour for every one of its ~60 ``sys.exit(1)`` sites. So
:func:`load_settings` is the one place that translates, and it is the only supported way to
build a :class:`CliSettings` from the environment.
"""

from __future__ import annotations

import sys
from difflib import get_close_matches
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, PositiveFloat, PositiveInt, ValidationError, field_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, NoDecode, SettingsConfigDict

from rmspec.domain.errors import InvalidSettingError

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from pydantic_settings import PydanticBaseSettingsSource

__all__ = [
    "HOMEBREW_LIBRARY_DIR",
    "NATIVE_LIBRARY_PATH_VAR",
    "CliSettings",
    "OcrEngineName",
    "Transport",
    "apply_native_library_path",
    "default_ssh_key_path",
    "default_sync_db_path",
    "load_settings",
]

NATIVE_LIBRARY_PATH_VAR = "DYLD_FALLBACK_LIBRARY_PATH"
"""The dynamic loader search path macOS consults, and the only variable this CLI sets.

Named rather than inlined because :func:`apply_native_library_path` and the test proving it
does nothing off macOS both need to agree on the spelling.
"""

HOMEBREW_LIBRARY_DIR = "/opt/homebrew/lib"
"""Where Homebrew puts ``libcairo``, ``libpango`` and the rest on Apple silicon.

``cairocffi`` resolves as an installed module and then dies in ``dlopen`` with ``OSError: no
library called "cairo-2" was found`` unless the loader can find this directory, which is
the third dependency state :class:`~rmspec.domain.ports.errors.DependencyProbe` exists to
distinguish.
"""

_ENV_PREFIX = "RMSPEC_"
"""The one spelling of the prefix, shared by the model config and the error rendering.

Two readers had to agree on it and did not: the environment source strips the prefix before
handing a field name to the model, and the dotenv source does not, so an unrecognised name
arrives as ``xochitle`` from one and ``rmspec_xochitle`` from the other. Measured, after the
first version of :func:`load_settings` reported ``RMSPEC_RMSPEC_XOCHITLE``.
"""

_INPUT_SHOULD_BE = "Input should be "
"""Prefix pydantic puts on a value complaint, stripped so the domain's sentence reads.

``InvalidSettingError`` assembles "setting X is 'v', which is not <requirement>", so the
requirement has to be a bare predicate -- "greater than 0", not "Input should be greater
than 0".
"""

_ENGINE_SEPARATOR = ","
"""How ``RMSPEC_OCR_ENGINES`` separates engines, shared by the parser and its docstring."""


class Transport(StrEnum):
    """How a command reaches the tablet, as a user spells it in the environment.

    Deliberately not :class:`~rmspec.domain.errors.TransportKind`. That enum is the domain's
    vocabulary for describing a transport in a capability report and its members are spelled
    for that job (``usb-web-api``, ``local-mirror``); this one is the three words a user types
    into ``RMSPEC_TRANSPORT``. :func:`rmspec.cli._container.composed_transport` is the single
    place the two are mapped, so neither has to be spelled for the other's audience.
    """

    USB = "usb"
    """The tablet's own HTTP server over USB ethernet. The default read path.

    ``GET /download/{id}/rmdoc`` is served *by* xochitl and is therefore a consistent
    snapshot by construction, which is the torn read that reading ``.rm`` off disk risks.
    """

    SSH = "ssh"
    """A shell session over USB ethernet, which reads the document tree directly.

    Still the only transport for the on-device search index and for the facts a USB run
    reports unsupported, so a ``usb`` run opens one of these as well.
    """

    MIRROR = "mirror"
    """A local copy of the document tree, needing no tablet. Not yet implemented."""


class OcrEngineName(StrEnum):
    """One recogniser a user may select in ``RMSPEC_OCR_ENGINES``.

    Notes
    -----
    Declaration order is the order the recognisers run in, because
    :class:`~rmspec.app.TranscribePages` reads its ``recognizers`` as a sequence and a
    ``frozenset`` has no order of its own. So this enum, not set iteration, is what makes a
    tiering decision reproducible between two runs of the same command.
    """

    BDA = "bda"
    """Bedrock Data Automation's sync document read. The default.

    First in declaration order, so on a run selecting more than one engine this is the tier-1
    reading the agreement short-circuit is measured against. It earns that on two counts
    measured against a real rmspec render: it reads the ink (148 characters, one error, and its
    own lowest word confidence marked exactly that error), and it reports a per-word confidence
    where Textract reports one per line.

    It is the one engine that needs configuration rather than only credentials --
    ``RMSPEC_BDA_PROJECT_ARN`` must name a SYNC-type project -- which is why ``rmspec doctor``
    reports it as restricted rather than letting a run discover that mid-page.
    """

    TEXTRACT = "textract"
    """AWS Textract. Needs no macOS, no native bindings and no project to be created first."""

    APPLE_VISION = "apple_vision"
    """The Vision framework's on-device handwriting recogniser. macOS only."""


def default_ssh_key_path() -> Path:
    """Give the SSH key path that reaches a stock reMarkable, expanded at call time.

    Returns
    -------
    Path
        ``~/.ssh/id_ed25519_remarkable``, resolved against the current user's home.

    Notes
    -----
    A function rather than a module constant so ``Path.home()`` is read when the settings
    object is built. A constant would freeze the home directory at import time, which makes
    the default untestable and wrong inside a ``monkeypatch.setenv`` for ``HOME``.
    """
    return Path.home() / ".ssh" / "id_ed25519_remarkable"


def default_sync_db_path() -> Path:
    """Give the sync store location, expanded at call time.

    Returns
    -------
    Path
        ``~/.remarkable-spec/sync.db``, the path the legacy tree already used, kept so an
        existing mirror is still found after the v0.2.0 rename.
    """
    return Path.home() / ".remarkable-spec" / "sync.db"


def apply_native_library_path(
    environ: MutableMapping[str, str],
    /,
    *,
    platform: str = sys.platform,
    library_dir: str = HOMEBREW_LIBRARY_DIR,
) -> str | None:
    """Point the macOS dynamic loader at Homebrew, unless the caller already has.

    Parameters
    ----------
    environ
        The environment to mutate, normally ``os.environ``. Passed in rather than reached
        for, so a test can hand over a plain ``dict``.
    platform
        The value of ``sys.platform`` to act on. Defaults to this interpreter's, and is a
        parameter so the non-macOS branch is reachable from a macOS test run.
    library_dir
        Directory to put on the loader's fallback search path.

    Returns
    -------
    str | None
        The value written to :data:`NATIVE_LIBRARY_PATH_VAR`, or ``None`` when nothing was
        written -- either because this is not macOS, or because the variable already held a
        value and a caller's explicit choice outranks this default.
    """
    if platform != "darwin":
        return None
    if environ.get(NATIVE_LIBRARY_PATH_VAR):
        return None
    environ[NATIVE_LIBRARY_PATH_VAR] = library_dir
    return library_dir


class _UnknownPrefixedEnvVars(EnvSettingsSource):
    """Offer the model every ``RMSPEC_*`` name no field claims, and nothing else.

    The narrowest thing that makes ``extra="forbid"`` mean what it says for the
    environment. It reports no declared field at all -- the standard ``EnvSettingsSource``
    stays in the chain behind it and remains the only reader of real values -- so this
    class cannot change how a valid setting is parsed. Its whole output is the set of
    orphans, which the model then rejects.
    """

    def __call__(self) -> dict[str, Any]:
        """Collect the prefixed environment names that map to no declared field.

        Returns
        -------
        dict[str, Any]
            Orphan names with the prefix stripped, mapped to their raw values. Empty in
            the ordinary case, which is why the standard source behind this one is
            untouched by it.
        """
        prefix = self.env_prefix.lower()
        claimed = {
            env_name.lower()
            for field_name, field in self.settings_cls.model_fields.items()
            for _, env_name, _ in self._extract_field_info(field, field_name)
        }
        return {
            name.lower().removeprefix(prefix): value
            for name, value in self.env_vars.items()
            if name.lower().startswith(prefix) and name.lower() not in claimed
        }


class CliSettings(BaseSettings):
    """Everything the composition root needs that a user may override.

    Notes
    -----
    Read from ``RMSPEC_``-prefixed environment variables and from a ``.env`` file in the
    working directory, with the environment winning. Build it with :func:`load_settings`
    rather than by calling it directly, so a bad value leaves as a domain error.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    xochitl: Path | None = None
    """Local mirror of a xochitl document tree, or ``None`` to require a device.

    ``RMSPEC_XOCHITL``. When set, the local-mirror catalog and bundle source can be bound
    and no tablet needs to be attached.
    """

    device_host: str = "10.11.99.1"
    """``RMSPEC_DEVICE_HOST`` -- the tablet's USB-ethernet address, fixed by firmware."""

    device_user: str = "root"
    """``RMSPEC_DEVICE_USER`` -- the only account the tablet's SSH daemon offers."""

    ssh_key: Path = Field(default_factory=default_ssh_key_path)
    """``RMSPEC_SSH_KEY`` -- private key for SSH, because paramiko ignores ``~/.ssh/config``."""

    sync_db: Path = Field(default_factory=default_sync_db_path)
    """``RMSPEC_SYNC_DB`` -- SQLite file holding the mirror, hashes and cached results."""

    render_dpi: PositiveInt = 229
    """``RMSPEC_RENDER_DPI`` -- raster density for ``rmspec render``: the Paper Pro's panel.

    229 is that panel's own density, so a default render is 1:1 rather than nearly so, and
    the arithmetic is here because it is what makes the number checkable:
    :data:`~rmspec.domain.models.PAPER_PRO_SCREEN` is 1620x2160, ``sqrt(1620**2 + 2160**2)``
    is 2700 pixels across an 11.8-inch diagonal, and ``2700 / 11.8`` is 228.8.

    **"1:1" is a claim about scale, not about the image's dimensions.** One panel pixel becomes
    one raster pixel; the raster is *not* 1620x2160. The renderer bounds its output by the ink
    plus a margin, not by the page, and ink may sit outside the nominal page -- measured on
    ``Test/TestNb``, whose strokes span 9.41 x 14.23 inches against a 7.07 x 9.43-inch panel and
    which rasterizes to 2155x3258. The check that this setting works is therefore the *ratio*:
    that page's SVG is 677.62 x 1024.35 points and its PNG 2155x3258 pixels, giving 3.1802
    px/pt against ``229 / 72 == 3.1806``. This paragraph exists because the sentence above was
    read as a size claim during the very session that corrected the number, so it will be read
    that way again.

    It was 226 until 2026-08-30 -- :data:`~rmspec.domain.models.RM2_SCREEN`'s density, the
    reMarkable 1 and 2 panel, inherited from the legacy ``RMSPEC_DPI`` that had no readers to
    notice which device it described -- and a Paper Pro page therefore came out 1599x2132
    rather than 1620x2160, 1.31% under the 1:1 this docstring already claimed. That is how a
    borrowed default hides, and it is why ``test_cli_settings.py`` now reads
    :data:`~rmspec.domain.models.PAPER_PRO_SCREEN` instead of retyping 229: the setting and
    the panel it is derived from cannot drift apart again without failing a test.
    """

    ocr_dpi: PositiveInt = 300
    """``RMSPEC_OCR_DPI`` -- the raster density the recognisers were tuned against."""

    thickness: PositiveFloat = 1.5
    """``RMSPEC_THICKNESS`` -- stroke weight multiplier compensating export versus screen."""

    max_pages: PositiveInt = 64
    """``RMSPEC_MAX_PAGES`` -- the entry-boundary work cap, and the reason it is a setting.

    Four requests take ``max_pages`` with no default, deliberately:
    ``PageSelection.resolve_against`` is the single enforcement point and its docstring says a
    silent default cap is the same surprise as legacy's silent last-page-only default. Supplying
    it from here is what stops one 432-page document from quietly becoming 432 model calls.
    """

    transport: Transport = Transport.USB
    """``RMSPEC_TRANSPORT`` -- ``usb``, ``ssh`` or ``mirror``. USB is the default read path.

    A setting rather than only a flag because the whole point of the ``RMSPEC_*`` surface is
    that ``eval "$(rmspec env)"`` reproduces a working shell. A per-command flag overrides it.
    """

    aws_region: str = "us-west-2"
    """``RMSPEC_AWS_REGION`` -- the region Textract and Bedrock are called in.

    One setting for both, because a composition that reads handwriting in one region and
    adjudicates it in another is a latency bill nobody asked for.
    """

    read_model: str = "global.openai.gpt-5.6-luna"
    """``RMSPEC_READ_MODEL`` -- OCR tier 2, the vision read of the raster itself.

    Tier 2 is asked to read the page, not to arbitrate: it sees the pixels and no other
    engine's answer, which is what keeps it an independent opinion for tier 3 to weigh.
    """

    merge_model: str = "global.openai.gpt-5.6-terra"
    """``RMSPEC_MERGE_MODEL`` -- OCR tier 3, which adjudicates tiers 0-2.

    Separate from :attr:`read_model` because the two jobs reward different models, and because
    binding one model twice would make "the reader and the judge disagreed" unobservable.
    """

    bda_project_arn: str | None = None
    """``RMSPEC_BDA_PROJECT_ARN`` -- the SYNC-type Data Automation project ``bda`` invokes.

    ``None`` rather than a default, because there is nothing to default to: a project is an
    account-scoped resource somebody has to create, and no API lists one to discover. Three
    things about it are not in the AWS user guide and were found by calling the operation: a
    project is mandatory despite the API member being optional, it must have
    ``projectType: SYNC`` rather than the ``ASYNC`` a console-created project defaults to, and
    such a project accepts exactly one document text format.

    Unset while ``bda`` is selected is refused while the recognisers are bound -- before any page
    is rendered, rasterised or sent to a model -- and the error names this variable. It is not
    refused before the document lookup, which a command performs first, and it is not yet a row in
    ``rmspec doctor``: that report is shaped around what a *transport* can do, and a missing
    setting is not that.
    """

    bda_profile: str = "us.data-automation-v1"
    """``RMSPEC_BDA_PROFILE`` -- the data automation profile id, joined onto the project's ARN.

    The operation requires a profile ARN whose partition, region and account must match the
    project's; those three are facts the project ARN already carries, so only this id is a
    choice. Measured working in ``us-west-2``. The ``us.`` prefix is a region family rather than
    a constant, and no ``ListDataAutomationProfiles`` operation exists, so a project outside the
    US regions needs this set by hand.
    """

    ocr_engines: Annotated[frozenset[OcrEngineName], NoDecode] = frozenset({OcrEngineName.BDA})
    """``RMSPEC_OCR_ENGINES`` -- comma-separated; ``apple_vision`` is macOS-only.

    ``NoDecode`` is load-bearing. pydantic-settings decodes a complex field's environment value
    as JSON by default, so without it ``RMSPEC_OCR_ENGINES=textract`` raises a ``SettingsError``
    -- which is not a ``ValidationError`` and would therefore escape :func:`load_settings`
    untranslated. With it the raw string reaches :meth:`_split_engine_names`.
    """

    agreement_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    """``RMSPEC_AGREEMENT_THRESHOLD`` -- the tier-0/tier-1 short-circuit.

    When the tablet's own index and the first recogniser agree at least this closely, the
    remaining tiers are not paid for. Not 1.0, because one hand is not every hand and an exact
    match is a stricter claim than "this page is already transcribed".
    """

    @field_validator("ocr_engines", mode="before")
    @classmethod
    def _split_engine_names(cls, value: object) -> object:
        """Read a comma-separated engine list, which is how a shell writes a set.

        Parameters
        ----------
        value
            Whatever the active source supplied. A ``str`` from the environment or a ``.env``
            file, thanks to ``NoDecode``; already a collection when a caller passed one to the
            constructor.

        Returns
        -------
        object
            A list of names for pydantic to coerce and validate, or ``value`` untouched when it
            was not a string. Empty entries are dropped, so a trailing comma is not an error.
        """
        if not isinstance(value, str):
            return value
        return [name.strip() for name in value.split(_ENGINE_SEPARATOR) if name.strip()]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the orphan-name source ahead of the standard environment source.

        Parameters
        ----------
        settings_cls
            The settings class being built, which the inserted source needs in order to
            know which names are claimed.
        init_settings
            Values passed to the constructor. Highest priority, unchanged.
        env_settings
            The standard environment source. Kept, and still the only reader of declared
            values -- the inserted source reports none of them.
        dotenv_settings
            The ``.env`` source, which already scans for orphans on its own.
        file_secret_settings
            The secrets-directory source, unused by this CLI and passed through.

        Returns
        -------
        tuple[PydanticBaseSettingsSource, ...]
            The default chain with :class:`_UnknownPrefixedEnvVars` spliced in above
            ``env_settings``.
        """
        return (
            init_settings,
            _UnknownPrefixedEnvVars(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )


def _variable_name(field: str, /) -> str:
    """Name the environment variable a rejected field came from.

    Parameters
    ----------
    field
        The field name pydantic reported, which carries the prefix when the value came from
        ``.env`` and does not when it came from the environment.

    Returns
    -------
    str
        The variable as a user typed it, prefixed exactly once.
    """
    upper = field.upper()
    if upper.startswith(_ENV_PREFIX):
        return upper
    return f"{_ENV_PREFIX}{upper}"


def _requirement_for(detail: Mapping[str, Any], /) -> str:
    """Phrase one pydantic complaint as the predicate the domain's message needs.

    Parameters
    ----------
    detail
        One entry from ``ValidationError.errors()``. Typed as a mapping rather than as
        ``pydantic_core.ErrorDetails`` because ``pydantic_core`` is pydantic's dependency and
        not one this package declares, and ``test_declared_dependencies.py`` fails an import
        of a distribution the package does not ask for.

    Returns
    -------
    str
        A bare predicate. For an unrecognised name, the fact plus the nearest declared
        setting when there is one -- which is what turns "RMSPEC_XOCHITLE did nothing" into
        "you meant RMSPEC_XOCHITL". For a bad value, pydantic's own words with its
        ``Input should be`` lead-in removed.
    """
    if detail["type"] != "extra_forbidden":
        return str(detail["msg"]).removeprefix(_INPUT_SHOULD_BE)
    requirement = "a setting name this CLI reads"
    field = str(detail["loc"][0]).lower().removeprefix(_ENV_PREFIX.lower())
    close = get_close_matches(field, tuple(CliSettings.model_fields), n=1)
    if not close:
        return requirement
    return f"{requirement}; the closest match is {_ENV_PREFIX}{close[0].upper()}"


def load_settings() -> CliSettings:
    """Build the settings from the environment, or fail in the domain's vocabulary.

    Returns
    -------
    CliSettings
        The validated settings for this process.

    Raises
    ------
    InvalidSettingError
        A ``RMSPEC_*`` variable is unrecognised, or its value is not one the setting
        accepts. Carries the variable, the value and the requirement as fields, so the
        error envelope renders it and :func:`~rmspec.domain.errors.exit_code` gives it
        ``EX_CONFIG`` rather than a bare ``1``.
    """
    try:
        return CliSettings()
    except ValidationError as exc:
        detail = exc.errors()[0]
        raise InvalidSettingError(
            setting=_variable_name(str(detail["loc"][0])),
            value=str(detail["input"]),
            requirement=_requirement_for(detail),
        ) from exc
