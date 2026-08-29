"""Where the sync database lives by default.

A function, not the legacy ``DEFAULT_DB_PATH`` module constant. A constant
resolves ``Path.home()`` at import time, which bakes the developer's own database
into every process that merely imports the package and makes the value
unpatchable in a test that sets ``HOME``.

No environment variable is read here either. ``RMSPEC_SYNC_DB`` is a settings
concern owned by ``rmspec-cli``, which resolves it and passes the path in --
persistence takes a path and nothing else.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["default_database_path"]


def default_database_path() -> Path:
    """Return the conventional location of the sync database.

    Returns
    -------
    Path
        ``~/.remarkable-spec/sync.db``, resolved against the current ``HOME``.
        Parent directories are created by
        :meth:`~rmspec.persistence._sqlite.SqliteDatabase.open`, not here.
    """
    return Path.home() / ".remarkable-spec" / "sync.db"
