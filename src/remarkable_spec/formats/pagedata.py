"""Parse reMarkable ``.pagedata`` text files.

A ``.pagedata`` file contains one template name per line, corresponding to each
page in a document (in order). The template name identifies the background grid
or pattern for that page.

Example ``.pagedata`` file::

    Blank
    Lined
    Lined
    Grid (small)

An empty template name or the string ``Blank`` means no background template.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "parse_pagedata",
]


def parse_pagedata(path: Path) -> list[str]:
    """Parse a ``.pagedata`` text file into a list of template names.

    Parameters
    ----------
    path:
        Path to a ``{UUID}.pagedata`` file.

    Returns
    -------
    list[str]
        List of template names, one per page.  Empty strings for pages
        with no explicit template.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return text.splitlines()
