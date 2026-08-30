"""Markdown to PDF, in the one module of this package that may load a native library.

``rmspec push notes.md`` is the north star in one command: an agent authors Markdown, the
tablet shows a document a human can read and annotate. This module is the middle step --
Markdown to HTML with ``markdown``, HTML to PDF with ``weasyprint`` -- and it exists as its
own module for a reason that is entirely about two tests pulling in opposite directions.

Why this is a separate module and not four lines inside ``_push.py``
-------------------------------------------------------------------
``tests/architecture/test_declared_dependencies.py`` requires every distribution a package
declares to be imported somewhere under that package's ``src/``, and it decides by
**AST-walking the source**. So ``importlib.import_module("weasyprint")`` does not satisfy it:
a real ``import`` statement has to exist, or ``rmspec-cli``'s ``push`` extra is an advertised
dependency nothing uses -- which is the defect that check was written for, after the legacy
tree declared ``pillow`` and imported ``PIL`` nowhere while a docstring promised a fallback.

But ``weasyprint`` links against ``libgobject`` and friends, and
``packages/rmspec-cli/tests/test_cli_entry.py`` proves that ``rmspec --help`` imports no
adapter and stays fast. A module-scope ``import weasyprint`` in a module that ``__init__.py``
reaches at registration time would put a ``dlopen`` on the path of ``--help``.

Both constraints are satisfied by exactly one arrangement, and the step-7 design freezes it:
the two imports live **here**, at module scope, and ``_push.py`` reaches this module through
``importlib.import_module("rmspec.cli._markdown")`` only once a ``.md`` is actually being
pushed. Neither library is on the adapter ban list -- that list names
``rmspec.{device,export,formats,ocr,persistence,render}`` and eleven third-party packages, and
these are not among them -- so the static half of the entry test is satisfied too.

The consequence a caller must respect: **importing this module can raise.** ``weasyprint`` is
declared ``native=True`` in ``_container.FEATURE_MODULES``, because it is installed and still
dies in ``dlopen`` when ``DYLD_FALLBACK_LIBRARY_PATH`` does not name the directory holding the
libraries -- measured on this machine, ``OSError: cannot load library 'libgobject-2.0-0'``,
which is the ``cairocffi`` failure with a different library name. So ``_push.py`` probes the
feature *before* it imports this module, and a user missing the extra gets
``MissingDependencyError`` with ``uv sync --extra push`` rather than an ``OSError`` from
inside a conversion they have already started.

The page count is the point, not a by-product
---------------------------------------------
:attr:`ConvertedDocument.page_count` is ``len(document.pages)`` -- the count from the renderer
that laid the pages out, which is the only component that can know it. It is not an estimate
and it is never a placeholder. ``CreateDocumentRequest.page_count`` refuses a zero, and the
prior art that rule exists for is a neighbouring project that uploads a valid, empty PDF
whenever its own parser silently returns nothing. Returning the count alongside the bytes,
from the one call that produced both, is what makes that refusal reachable here: a Markdown
file with no renderable content produces a document with no pages, and this layer reports that
instead of handing on bytes that look fine.

The page is the tablet's, not A4
--------------------------------
``@page size`` comes from :data:`~rmspec.domain.models.PAPER_PRO_SCREEN`'s own millimetre
dimensions, so the PDF arrives at the size of the screen that will display it and the reader
does no scaling. A4 would be a page shape chosen for a printer that is not in this story.
"""

from __future__ import annotations

from typing import Final

import markdown
import weasyprint
from pydantic import BaseModel, Field

from rmspec.domain.models import PAPER_PRO_SCREEN

__all__ = [
    "MARKDOWN_EXTENSIONS",
    "PAGE_STYLESHEET",
    "ConvertedDocument",
    "to_pdf",
]

MARKDOWN_EXTENSIONS: Final = ("extra", "sane_lists", "smarty")
"""The ``markdown`` extensions every conversion runs, and none that needs a further install.

``extra`` is the bundle that makes authored Markdown behave the way its author expects --
tables, fenced code, footnotes, definition lists, attribute lists. ``sane_lists`` stops a
numbered list restarting because a blank line fell in the wrong place. ``smarty`` turns quotes
and dashes into their typographic forms, which matters more here than usual because the output
is read rather than diffed.

``codehilite`` is deliberately absent: it needs ``pygments``, which ``rmspec-cli`` does not
declare, and the architecture check above would then fail in the opposite direction.
"""

PAGE_STYLESHEET: Final = f"""
@page {{
  size: {PAPER_PRO_SCREEN.width_mm:.1f}mm {PAPER_PRO_SCREEN.height_mm:.1f}mm;
  margin: 14mm 12mm 16mm 12mm;
  @bottom-center {{ content: counter(page); font-size: 8pt; color: #666; }}
}}
html {{ font-size: 11pt; }}
body {{
  font-family: "Noto Sans", "Helvetica Neue", sans-serif;
  line-height: 1.45;
  margin: 0;
  color: #111;
}}
h1, h2, h3, h4 {{ line-height: 1.2; page-break-after: avoid; }}
h1 {{ font-size: 1.7rem; margin: 0 0 0.6rem; }}
h2 {{ font-size: 1.35rem; margin: 1.4rem 0 0.5rem; }}
h3 {{ font-size: 1.1rem; margin: 1.1rem 0 0.4rem; }}
p, ul, ol, dl {{ margin: 0 0 0.7rem; }}
li {{ margin: 0 0 0.2rem; }}
code, pre {{ font-family: "Menlo", "DejaVu Sans Mono", monospace; font-size: 0.85rem; }}
pre {{
  background: #f4f4f4;
  padding: 0.6rem 0.7rem;
  border-radius: 2mm;
  white-space: pre-wrap;
  page-break-inside: avoid;
}}
blockquote {{
  margin: 0 0 0.7rem;
  padding-left: 0.8rem;
  border-left: 2px solid #bbb;
  color: #333;
}}
table {{ border-collapse: collapse; width: 100%; margin: 0 0 0.8rem; }}
th, td {{ border: 1px solid #bbb; padding: 0.25rem 0.4rem; text-align: left; }}
img {{ max-width: 100%; }}
"""
"""The whole stylesheet, inlined into the document rather than passed as a separate sheet.

Inlined because ``weasyprint``'s ``stylesheets=`` argument has moved between major versions
while a ``<style>`` element has not, and this module has one job that must not break on a
dependency bump. ``@page size`` is the tablet's own page; see this module's docstring.
"""

_DOCUMENT_TEMPLATE: Final = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{stylesheet}</style>
</head>
<body>
{body}
</body>
</html>
"""
"""The HTML shell the converted body is placed in.

A whole document rather than a fragment: ``weasyprint`` will render a fragment, but the
``<title>`` becomes the PDF's own document title and a declared charset is what keeps a
non-ASCII heading from depending on the renderer's guess.
"""


class ConvertedDocument(BaseModel, frozen=True, extra="forbid"):
    """A rendered PDF and the page count of the renderer that produced it.

    Both together and neither alone, because the caller needs the count to build a
    ``CreateDocumentRequest`` and nothing downstream of here can recover it from the bytes.
    """

    data: bytes
    """The complete PDF payload, ready to upload."""

    page_count: int = Field(ge=0)
    """How many pages the renderer laid out.

    Zero when the Markdown carried nothing renderable, which is the state the upload path
    refuses rather than delivering an empty document to a device with no delete route.
    """


def to_pdf(text: str, /, *, title: str, base_url: str | None = None) -> ConvertedDocument:
    """Convert Markdown source to a PDF sized for the tablet.

    Parameters
    ----------
    text
        The Markdown source, already decoded.
    title
        The document title to record in the PDF's metadata. Not the name the tablet will
        show -- that comes from the multipart filename, which is the upload adapter's
        business and is reported on ``CreateDocumentResult.visible_name``.
    base_url
        What relative links and image sources resolve against, normally the directory the
        Markdown file lives in, or ``None`` to resolve nothing. Passing the directory is
        what lets an authored document carry its own figures.

    Returns
    -------
    ConvertedDocument
        The PDF bytes and the count of pages the renderer produced.
    """
    body = markdown.markdown(text, extensions=list(MARKDOWN_EXTENSIONS), output_format="html")
    source = _DOCUMENT_TEMPLATE.format(title=title, stylesheet=PAGE_STYLESHEET, body=body)
    document = weasyprint.HTML(string=source, base_url=base_url).render()
    return ConvertedDocument(data=document.write_pdf(), page_count=len(document.pages))
