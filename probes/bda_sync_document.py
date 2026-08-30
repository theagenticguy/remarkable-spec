"""Settle by measurement whether sync ``InvokeDataAutomation`` accepts a document. Costs money.

Run by hand, never by a test, and never by CI:

    uv run python probes/bda_sync_document.py

Why it lives outside ``packages/``
---------------------------------
``packages/rmspec-ocr/tests/`` forbids by construction everything this file does. Its whole point
is that no test builds an AWS client, carries a credential chain, or spends a cent, so an
adapter's seams are injected and its contract runs against doubles. That discipline is what makes
the suite runnable offline on any machine -- and it also means the suite can never answer a
question about what the *real* service does. This does, for about one cent, and it is kept out of
``packages/`` so it enters neither the coverage floor, the architecture invariants, nor the
no-billable-calls rule that would have to grow an exception for it.

What it settled, on 2026-08-30
-----------------------------
``bda-using-api`` says ``InvokeDataAutomation`` "only supports processing images" and warns that
an image "semantically classified as a document" raises an error, while ``bda-limits`` and
``bda-output-documents`` publish sync *document* requirements and a sync document response. This
returned ``semanticModality: "DOCUMENT"`` with a full document standard output and no error, on a
PNG whose own ``metadata.file_type`` was ``IMAGE``. So the sentence is stale.

Three preconditions the user guide never states, each of which this found by being refused:

- ``dataAutomationConfiguration`` is optional in the API model and mandatory in fact:
  ``At least one of project or inline blueprints must be provided in the request``.
- Projects carry a ``projectType`` of ``ASYNC`` or ``SYNC`` and this operation refuses the former:
  ``Sync API only supports SYNC project type``. Neither appears on the page above.
- A SYNC project accepts exactly one document text format:
  ``Sync project standard output configuration cannot have more than 1 document text format
  types``.

And two facts the published response schema omits, which decided how
:mod:`rmspec.ocr.bda` reads a response: ``text_lines`` and ``text_words`` both carry a
``confidence``, and only the word-level one is a measurement. Every line came back at exactly
``0.01`` while the words beneath them ran 0.869 to 1.0 -- and the lowest word, ``0.869``, was the
one token the service actually got wrong.

The subject is a real render
----------------------------
Ink engraved by :func:`rmspec.render.text_to_ink` at ``rmspec reply``'s own defaults, laid out by
:class:`~rmspec.render.SvgPageRenderer`, rasterised by
:class:`~rmspec.export.CairoSvgRasterizer` at ``RMSPEC_OCR_DPI``. Not a stock document: the
question worth a billable call is what BDA does with *this* CLI's output.

It creates a throwaway SYNC project, invokes it, and deletes the project in a ``finally`` --
whatever happened, and including when the invoke is refused.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import boto3
from botocore.exceptions import ClientError

from rmspec.domain.models import (
    EXPORT_PALETTE,
    PAPER_PRO_SCREEN,
    Layer,
    Page,
    PageContent,
    PageId,
    PenColor,
)
from rmspec.domain.ports.export import PhysicalSize, SvgPage
from rmspec.domain.ports.render import RenderStyle, TextStyle
from rmspec.export import CairoSvgRasterizer
from rmspec.render import (
    LEGACY_MIN_PADDING_MM,
    LEGACY_THICKNESS_SCALE,
    SVG_RENDERER_REVISION,
    InkTextStyle,
    SvgPageRenderer,
    text_to_ink,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def say(message: str, /) -> None:
    """Write one line for whoever ran this by hand.

    Parameters
    ----------
    message
        The line, without its newline.

    Notes
    -----
    ``sys.stdout.write`` rather than ``print`` for the same reason every other module in this
    repository uses it: ``print`` is banned by the lint configuration, and the exemption a
    ``noqa`` would need is a shortcut this repository does not allow anywhere.
    """
    sys.stdout.write(f"{message}\n")


REGION = "us-west-2"
OCR_DPI = 300
PROJECT_NAME = "rmspec-sync-document-probe"
PROFILE = "us.data-automation-v1"
POLL_ATTEMPTS = 30
POLL_SECONDS = 2.0
OUTPUT_DIR = Path(tempfile.gettempdir())
"""Where the rendered page and the decoded output are dropped, for a human to look at."""

NOTE = (
    "Meeting notes 30 August\n"
    "Ship the single wheel today.\n"
    "Ask Priya about the OCR budget.\n"
    "Tier 0 is free, tier 1 is not.\n"
    "Settle the sync contradiction."
)

#: Everything that answers a question and nothing that costs latency. WORD and LINE granularity
#: are what make the confidence question answerable at all; generative fields are off because a
#: 250-word summary of a five-line note answers nothing and the service's own advice is to
#: disable them for speed.
DOCUMENT_CONFIG: Mapping[str, Any] = {
    "document": {
        "extraction": {
            "granularity": {"types": ["PAGE", "ELEMENT", "WORD", "LINE"]},
            "boundingBox": {"state": "ENABLED"},
        },
        "generativeField": {"state": "DISABLED"},
        # Exactly one. Two is refused with "cannot have more than 1 document text format types".
        "outputFormat": {
            "textFormat": {"types": ["PLAIN_TEXT"]},
            "additionalFileFormat": {"state": "DISABLED"},
        },
    },
}


def rendered_png() -> bytes:
    """Engrave the note as ink and rasterise it exactly as ``rmspec ocr`` would.

    Returns
    -------
    bytes
        A PNG of the rendered page.
    """
    ink = text_to_ink(
        NOTE,
        screen=PAPER_PRO_SCREEN,
        style=InkTextStyle(em_mm=5.0, line_height=1.4, color=PenColor.BLACK, thickness_scale=2.0),
        left_mm=15.0,
        top_mm=15.0,
        width_mm=150.0,
    )
    page = Page(
        page_id=PageId(uuid=str(UUID(int=0))),
        index=0,
        content=PageContent(
            layers=(Layer(name="Layer 1", visible=True, strokes=ink.strokes, text_blocks=()),),
            text_blocks=(),
            defects=(),
        ),
    )
    rendered = SvgPageRenderer().render(
        page,
        screen=PAPER_PRO_SCREEN,
        palette=EXPORT_PALETTE,
        style=RenderStyle(
            thickness_scale=LEGACY_THICKNESS_SCALE,
            min_padding_mm=LEGACY_MIN_PADDING_MM,
            text=TextStyle(family="sans-serif", size_px=32.0, line_height=1.2),
            renderer_revision=SVG_RENDERER_REVISION,
        ),
    )
    raster = CairoSvgRasterizer().to_png(
        # ports.render.PhysicalSize and ports.export.PhysicalSize are distinct types with the
        # same name and fields: each slice owns its own value object, so this crosses over.
        SvgPage(
            page_ref=rendered.page_ref,
            svg=rendered.svg,
            size=PhysicalSize(width_mm=rendered.size.width_mm, height_mm=rendered.size.height_mm),
        ),
        dpi=OCR_DPI,
    )
    say(
        f"probe page: {len(ink.strokes)} strokes -> {raster.width}x{raster.height} px, "
        f"{len(raster.data)} bytes of PNG at {raster.render_dpi} dpi"
    )
    return raster.data


def report(parsed: Mapping[str, Any]) -> None:
    """Print what one standard output says about documents, ink and confidence.

    Parameters
    ----------
    parsed
        One decoded ``standardOutput``.
    """
    say(f"    top-level keys: {sorted(parsed)}")
    for key in ("text_lines", "text_words", "elements", "pages"):
        value = parsed.get(key)
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            continue
        say(f"    {key}: {len(value)} entries, keys {sorted(value[0])}")
        say(f"      carries a confidence: {'confidence' in value[0]}")
        if key == "text_lines":
            say(f"      line confidences: {[entry.get('confidence') for entry in value]}")
        if key == "text_words":
            scores = sorted(entry.get("confidence") for entry in value)
            say(f"      word confidences: min {scores[0]}, max {scores[-1]}")
        if key == "pages":
            representation = value[0].get("representation") or {}
            say(f"    --- the text BDA read from the ink ---\n{representation.get('text')}")


def main() -> int:
    """Create a SYNC document project, invoke it on a rendered page, then delete the project.

    Returns
    -------
    int
        ``0`` when the service answered, ``1`` when it refused. A refusal is a result too: the
        message is the finding, which is how the three undocumented preconditions were found.
    """
    png = rendered_png()
    (OUTPUT_DIR / "bda_probe_page.png").write_bytes(png)

    session = boto3.Session(region_name=REGION)
    account = session.client("sts").get_caller_identity()["Account"]
    profile = f"arn:aws:bedrock:{REGION}:{account}:data-automation-profile/{PROFILE}"
    build = session.client("bedrock-data-automation")
    runtime = session.client("bedrock-data-automation-runtime")

    say(f"\ncreating a SYNC-type project with document modality: {PROJECT_NAME}")
    try:
        created = build.create_data_automation_project(
            projectName=PROJECT_NAME,
            projectDescription="Throwaway: does sync InvokeDataAutomation accept a document?",
            projectStage="LIVE",
            projectType="SYNC",
            standardOutputConfiguration=DOCUMENT_CONFIG,
        )
    except ClientError as exc:
        say(f"  REFUSED at creation -- {exc.response['Error']['Code']}: {exc}")
        return 1
    project_arn = created["projectArn"]
    say(f"  created: {project_arn}")

    try:
        status = "UNKNOWN"
        for _ in range(POLL_ATTEMPTS):
            project = build.get_data_automation_project(projectArn=project_arn)["project"]
            status = project["status"]
            if status != "IN_PROGRESS":
                break
            time.sleep(POLL_SECONDS)
        say(f"  project status: {status}")

        say("\ncalling InvokeDataAutomation with the rendered ink page")
        try:
            response = runtime.invoke_data_automation(
                inputConfiguration={"bytes": png},
                dataAutomationProfileArn=profile,
                dataAutomationConfiguration={
                    "dataAutomationProjectArn": project_arn,
                    "stage": "LIVE",
                },
            )
        except ClientError as exc:
            say(f"  REFUSED at invoke -- {exc.response['Error']['Code']}: {exc}")
            return 1

        say(f"\n  semanticModality: {response['semanticModality']!r}")
        for index, segment in enumerate(response.get("outputSegments", [])):
            raw = segment["standardOutput"]
            say(f"  segment {index}: standardOutput is a {type(raw).__name__}")
            parsed = json.loads(raw)
            destination = OUTPUT_DIR / f"bda_probe_output_{index}.json"
            destination.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
            report(parsed)
            say(f"  full output at {destination}")
        return 0
    finally:
        build.delete_data_automation_project(projectArn=project_arn)
        say(f"\ndeleted the throwaway project {project_arn}")


if __name__ == "__main__":
    raise SystemExit(main())
