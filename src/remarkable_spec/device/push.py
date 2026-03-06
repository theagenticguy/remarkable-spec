"""Content rendering for smart push to reMarkable.

Converts non-native file types (Markdown, Mermaid, plain text) to PDF
so they can be pushed to the device. The device natively handles
PDF and EPUB — this module bridges the gap for other content.

Renderers:
- ``.md`` → PDF via markdown + weasyprint
- ``.mmd`` → PDF via ``mmdc`` CLI (Mermaid)
- ``.txt`` → PDF via weasyprint (monospace)

Requires the ``[push]`` extra for Markdown/text rendering::

    uv add 'remarkable-spec[push]'

Mermaid rendering requires ``mmdc`` (mermaid-cli) installed globally::

    npm install -g @mermaid-js/mermaid-cli
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path


def render_to_pdf(source: Path) -> Path:
    """Detect file type by extension and render to PDF.

    Args:
        source: Path to the source file.

    Returns:
        Path to a temporary PDF file. Caller is responsible for cleanup.

    Raises:
        ValueError: If the file extension is not supported.
        FileNotFoundError: If the source file doesn't exist.
        ImportError: If required rendering dependencies are missing.
        RuntimeError: If rendering fails.
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    suffix = source.suffix.lower()
    renderer = _RENDERERS.get(suffix)
    if renderer is None:
        raise ValueError(
            f"Unsupported file type: {suffix}. Supported: {', '.join(_RENDERERS.keys())}"
        )

    return renderer(source)


def _render_markdown(source: Path) -> Path:
    """Render Markdown to PDF via weasyprint.

    Converts markdown to HTML first, then uses weasyprint for PDF generation.
    The result is a clean, well-formatted PDF suitable for reading on
    the reMarkable's e-ink display.
    """
    try:
        import markdown
        import weasyprint
    except ImportError:
        raise ImportError(
            "Markdown rendering requires 'markdown' and 'weasyprint'. "
            "Install with: uv add 'remarkable-spec[push]'"
        ) from None

    md_text = source.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "codehilite"])

    html = f"""\
<!DOCTYPE html>
<html>
<head>
<style>
    body {{
        font-family: serif;
        font-size: 12pt;
        line-height: 1.6;
        max-width: 6in;
        margin: 0.75in auto;
        color: #000;
    }}
    code, pre {{
        font-family: monospace;
        font-size: 10pt;
        background: #f5f5f5;
        padding: 2px 4px;
    }}
    pre {{
        padding: 8px 12px;
        overflow-x: auto;
    }}
    h1, h2, h3 {{ font-family: sans-serif; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
    th {{ background: #f0f0f0; }}
</style>
</head>
<body>{html_body}</body>
</html>"""

    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    output = Path(tmp)
    weasyprint.HTML(string=html).write_pdf(str(output))
    return output


def _render_mermaid(source: Path) -> Path:
    """Render Mermaid diagram to PDF via ``mmdc`` CLI.

    Requires mermaid-cli to be installed globally::

        npm install -g @mermaid-js/mermaid-cli
    """
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    output = Path(tmp)

    try:
        result = subprocess.run(
            ["mmdc", "--input", str(source), "--output", str(output), "--pdfFit"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Mermaid rendering requires 'mmdc' (mermaid-cli). "
            "Install with: npm install -g @mermaid-js/mermaid-cli"
        ) from None

    if result.returncode != 0:
        raise RuntimeError(f"Mermaid rendering failed: {result.stderr.strip()}")

    return output


def _render_text(source: Path) -> Path:
    """Render plain text to PDF via weasyprint with monospace font.

    Simple monospace layout suitable for reading on the reMarkable.
    """
    try:
        import weasyprint
    except ImportError:
        raise ImportError(
            "Text rendering requires 'weasyprint'. Install with: uv add 'remarkable-spec[push]'"
        ) from None

    text = source.read_text(encoding="utf-8")
    # Escape HTML entities
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    html = f"""\
<!DOCTYPE html>
<html>
<head>
<style>
    body {{
        font-family: monospace;
        font-size: 10pt;
        line-height: 1.4;
        max-width: 6.5in;
        margin: 0.5in auto;
        color: #000;
        white-space: pre-wrap;
        word-wrap: break-word;
    }}
</style>
</head>
<body><pre>{escaped}</pre></body>
</html>"""

    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    output = Path(tmp)
    weasyprint.HTML(string=html).write_pdf(str(output))
    return output


_RENDERERS: dict[str, Callable[[Path], Path]] = {
    ".md": _render_markdown,
    ".mmd": _render_mermaid,
    ".txt": _render_text,
}
