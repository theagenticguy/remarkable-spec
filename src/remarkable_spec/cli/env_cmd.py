"""``rmspec env`` -- Print shell environment for external tools.

Prints the environment variables needed to use reMarkable-related tools
(cairo, weasyprint, etc.) outside of the rmspec CLI.

Example::

    eval "$(rmspec env)"
    weasyprint notes.html notes.pdf   # now works without manual DYLD config
"""

from __future__ import annotations

import os
import platform

import cyclopts
from rich.console import Console

from remarkable_spec.cli._util import get_xochitl_dir, settings

app = cyclopts.App(name="env", help=__doc__)
console = Console()


@app.default
def env(
    *,
    shell: bool = True,
) -> None:
    """Print environment variables for shell integration.

    Outputs ``export`` statements that can be eval'd to configure the
    current shell for reMarkable tooling.
    """
    exports: list[tuple[str, str]] = []

    # Xochitl directory
    xochitl = get_xochitl_dir()
    if xochitl is not None:
        exports.append(("RMSPEC_XOCHITL", str(xochitl)))

    # Device settings
    exports.append(("RMSPEC_DEVICE_HOST", settings.device_host))

    # DYLD for cairo/weasyprint on macOS
    if platform.system() == "Darwin":
        dyld = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if not dyld:
            from pathlib import Path

            brew_lib = Path("/opt/homebrew/lib")
            if brew_lib.exists():
                dyld = str(brew_lib)
        if dyld:
            exports.append(("DYLD_FALLBACK_LIBRARY_PATH", dyld))

    if shell:
        for key, value in exports:
            console.print(f"export {key}={value!r}")
    else:
        for key, value in exports:
            console.print(f"{key}={value}")
