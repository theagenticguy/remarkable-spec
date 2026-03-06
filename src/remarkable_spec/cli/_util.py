"""Shared CLI configuration via pydantic-settings."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RmspecSettings(BaseSettings):
    """Global settings for the rmspec CLI.

    Values are resolved in order: explicit keyword → environment variable → default.
    A ``.env`` file in the current directory is also read automatically.

    Environment variables are prefixed with ``RMSPEC_`` and uppercased.
    """

    model_config = SettingsConfigDict(
        env_prefix="RMSPEC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    xochitl: Path | None = Field(
        default=None,
        description="Path to the xochitl data directory. "
        "Set via --xochitl flag or RMSPEC_XOCHITL env var.",
    )
    device_host: str = Field(
        default="10.11.99.1",
        description="reMarkable device IP address for SSH/HTTP access. "
        "Set via RMSPEC_DEVICE_HOST env var.",
    )
    device_user: str = Field(
        default="root",
        description="SSH username for device access. Set via RMSPEC_DEVICE_USER env var.",
    )
    device_password: str | None = Field(
        default=None,
        description="SSH password for device access. Set via RMSPEC_DEVICE_PASSWORD env var.",
    )
    thickness: float = Field(
        default=1.5,
        description="Default stroke thickness multiplier for rendering. "
        "Set via RMSPEC_THICKNESS env var.",
    )
    dpi: int = Field(
        default=226,
        description="Default DPI for raster export. Set via RMSPEC_DPI env var.",
    )
    sync_db: Path | None = Field(
        default=None,
        description="Path to the SQLite sync database. "
        "Defaults to ~/.remarkable-spec/sync.db. Set via RMSPEC_SYNC_DB.",
    )


# Singleton — instantiated once, reads env vars + .env on import
settings = RmspecSettings()


# Auto-configure macOS Homebrew cairo library path so cairosvg/cairocffi
# can find libcairo without the user exporting DYLD_FALLBACK_LIBRARY_PATH.
if platform.system() == "Darwin" and "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
    _brew_lib = Path("/opt/homebrew/lib")
    if _brew_lib.exists():
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = str(_brew_lib)


def get_sync_db():
    """Get a SyncDB instance using the configured path.

    Lazily imports to avoid requiring sqlite3 at CLI startup.
    """
    from remarkable_spec.sync.db import SyncDB

    return SyncDB(settings.sync_db)


def get_xochitl_dir(explicit: Path | None = None) -> Path | None:
    """Resolve xochitl directory from explicit arg, env var, or .env file.

    Resolution order:

    1. Explicit CLI flag (``--xochitl``)
    2. ``RMSPEC_XOCHITL`` environment variable (via pydantic-settings)
    3. Default location ``~/.remarkable-spec/xochitl/`` (if it exists)

    Parameters
    ----------
    explicit:
        An explicitly provided path (e.g. from a CLI flag). Takes priority.

    Returns
    -------
    Path | None
        The resolved xochitl directory, or ``None`` if not configured.
    """
    if explicit is not None:
        return explicit
    if settings.xochitl is not None:
        return settings.xochitl
    # Fallback: default sync location
    default = Path("~/.remarkable-spec/xochitl").expanduser()
    if default.is_dir():
        return default
    return None
