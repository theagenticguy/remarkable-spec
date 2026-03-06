"""rmspec -- CLI for reMarkable tablet file formats, rendering, and device sync.

Quick start::

    rmspec inspect page.rm           # inspect .rm file contents
    rmspec ls ~/xochitl/             # list documents in a backup
    rmspec render "My Notes" out/    # render pages to SVG/PNG/PDF
    rmspec ocr "My Notes" --page 3   # handwriting OCR
    rmspec diagram "Notes" --page 1  # extract Mermaid from diagrams
    rmspec sync pull                 # incremental sync from device

Installation::

    uv add remarkable-spec                     # parsing + CLI
    uv add 'remarkable-spec[render]'           # + SVG/PNG/PDF export
    uv add 'remarkable-spec[device]'           # + SSH/USB device access
    uv add 'remarkable-spec[ocr,render]'       # + handwriting OCR
    uv add 'remarkable-spec[all]'              # everything
"""

from __future__ import annotations

import cyclopts

from remarkable_spec.cli._util import get_xochitl_dir, settings
from remarkable_spec.cli.annotations_cmd import app as annotations_app
from remarkable_spec.cli.device_cmd import app as device_app
from remarkable_spec.cli.diagram_cmd import app as diagram_app
from remarkable_spec.cli.env_cmd import app as env_app
from remarkable_spec.cli.inspect_cmd import app as inspect_app
from remarkable_spec.cli.ls_cmd import app as ls_app
from remarkable_spec.cli.ocr_cmd import app as ocr_app
from remarkable_spec.cli.render_cmd import app as render_app
from remarkable_spec.cli.search_cmd import app as search_app
from remarkable_spec.cli.sync_cmd import app as sync_app
from remarkable_spec.cli.tree_cmd import app as tree_app

__all__ = ["app", "get_xochitl_dir", "settings"]

app = cyclopts.App(
    name="rmspec",
    help=__doc__,
    version_flags=("--version", "-V"),
)


# Register subcommands
app.command(inspect_app, name="inspect")
app.command(ls_app, name="ls")
app.command(render_app, name="render")
app.command(tree_app, name="tree")
app.command(ocr_app, name="ocr")
app.command(diagram_app, name="diagram")
app.command(search_app, name="search")
app.command(sync_app, name="sync")
app.command(device_app, name="device")
app.command(annotations_app, name="annotations")
app.command(env_app, name="env")


def _get_version() -> str:
    """Get the package version."""
    try:
        from importlib.metadata import version

        return version("remarkable-spec")
    except Exception:
        return "0.0.0-dev"


# Set version
app.version = _get_version()
