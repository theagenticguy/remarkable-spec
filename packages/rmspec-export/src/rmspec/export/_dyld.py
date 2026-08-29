"""Make ``cairocffi``'s library search find a Homebrew ``libcairo`` on macOS.

Why this module exists, with the measurement that justifies it
-------------------------------------------------------------
``cairocffi`` resolves its shared library at *import* time by calling
:func:`ctypes.util.find_library` for ``cairo-2``, ``cairo`` and ``libcairo-2``, then
``ffi.dlopen`` on whatever absolute path comes back. On this machine, with
``DYLD_FALLBACK_LIBRARY_PATH`` unset, all three lookups miss and the import dies with
``OSError: no library called "cairo-2" was found`` -- an error that reads like a missing
wheel when the wheel is installed and only the search path is wrong.

The received wisdom is that this cannot be repaired from inside Python, because ``dyld``
reads ``DYLD_*`` once at ``exec``. That is true of ``dlopen("libcairo.2.dylib")`` and it is
*not* true of the path ``cairocffi`` actually takes: on Darwin
:func:`ctypes.util.find_library` is pure Python (:mod:`ctypes.macholib.dyld`) and re-reads
:data:`os.environ` on every call. Measured on this machine, in one interpreter::

    find_library("cairo") -> None                       # before
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = "/opt/homebrew/lib"
    find_library("cairo") -> /opt/homebrew/lib/libcairo.dylib   # after
    cairocffi.cairo_version_string() -> "1.18.4"

So an in-process assignment made *before* ``cairocffi`` is imported is sufficient, and it
is the only mechanism available to a library that cannot dictate how it is launched. The
alternative -- setting the variable in ``mise.toml`` ``[env]`` -- is not equivalent: a
SIP-protected shell in the launch chain strips ``DYLD_*`` before the interpreter ever
starts, so it works on some invocations and silently does not on others.

Scope of the side effect
------------------------
:func:`ensure_native_library_path` writes exactly one environment variable, only when it is
unset, only on Darwin, and only with directories that exist on the filesystem. If the
variable is already set -- by a launcher, a container, or a developer -- it is left alone.
:func:`fallback_library_path` is the whole decision as a pure function, so the policy is
table-tested without touching :data:`os.environ` or any native library.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DYLD_FALLBACK_VARIABLE",
    "HOMEBREW_LIBRARY_DIRECTORIES",
    "ensure_native_library_path",
    "fallback_library_path",
]

DYLD_FALLBACK_VARIABLE = "DYLD_FALLBACK_LIBRARY_PATH"
"""The one variable this module may write."""

HOMEBREW_LIBRARY_DIRECTORIES: tuple[str, ...] = ("/opt/homebrew/lib", "/usr/local/lib")
"""Homebrew's ``lib`` prefixes, Apple Silicon first, then Intel.

Not a guess: these are the two prefixes ``brew --prefix`` reports on the two macOS
architectures, and ``libcairo.dylib`` is a symlink inside one of them on any machine where
``brew install cairo`` has run.
"""


def fallback_library_path(
    current: str | None,
    *,
    platform: str,
    candidates: tuple[str, ...] = HOMEBREW_LIBRARY_DIRECTORIES,
    exists: Callable[[str], bool] | None = None,
) -> str | None:
    """Decide what ``DYLD_FALLBACK_LIBRARY_PATH`` should become, or ``None`` for no change.

    Parameters
    ----------
    current
        The variable's present value, or ``None`` when it is unset.
    platform
        A :data:`sys.platform` string. Only ``"darwin"`` is acted on; ``dyld`` does not
        exist anywhere else, and Linux resolves ``libcairo.so.2`` through ``ld.so``
        without help.
    candidates
        Directories to offer, in preference order.
    exists
        Predicate deciding whether a candidate directory is present. Defaults to
        :meth:`pathlib.Path.is_dir`. Injected so the policy is testable without a
        filesystem shaped like this machine's.

    Returns
    -------
    str | None
        The value to assign, or ``None`` when the variable must be left as it is --
        because it is already set, because this is not macOS, or because none of the
        candidate directories exist.
    """
    if platform != "darwin":
        return None
    if current:
        return None
    probe = exists if exists is not None else (lambda directory: Path(directory).is_dir())
    present = [directory for directory in candidates if probe(directory)]
    if not present:
        return None
    return os.pathsep.join(present)


def ensure_native_library_path() -> str | None:
    """Apply :func:`fallback_library_path` to this process's environment, once.

    Called from :mod:`rmspec.export._cairo` immediately before ``cairocffi`` is imported,
    which is the only moment at which it can help.

    Returns
    -------
    str | None
        The value written, or ``None`` when nothing was changed.
    """
    value = fallback_library_path(os.environ.get(DYLD_FALLBACK_VARIABLE), platform=sys.platform)
    if value is not None:
        os.environ[DYLD_FALLBACK_VARIABLE] = value
    return value
