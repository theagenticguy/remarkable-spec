"""OCR adapters: Apple's on-device Vision framework, AWS Textract, and Bedrock.

This package binds the two Protocols in :mod:`rmspec.domain.ports.ocr` --
:class:`~rmspec.domain.ports.ocr.VisionLanguageModel` and
:class:`~rmspec.domain.ports.ocr.TextRecognizer` -- to the three engines this workspace has
measured, and supplies the composition root's health check for the one engine that is optional.
It may import ``rmspec.domain`` and nothing else from the workspace.

It is not a use case. It does not decide how many recognizers to run, what to do when one of
them fails, or whether a truncated answer is acceptable -- and it cannot, because every one of
those is reported here as a typed value rather than acted on: truncation and refusal arrive as
:class:`~rmspec.domain.ports.ocr.StopReason` on a returned completion, a blank page arrives as
``text=""``, and a failure arrives as one of the six errors the ports name. Deciding what any of
them means is ``rmspec.app``'s job.

What is exported, and what stays behind an underscore
----------------------------------------------------
Three adapters -- :class:`~rmspec.ocr.vision_model.BedrockOpenAiVisionModel` for the model port,
:class:`~rmspec.ocr.apple_vision.AppleVisionRecognizer` and
:class:`~rmspec.ocr.textract.TextractRecognizer` for the recognition port -- plus the two names a
container needs to *decide* it can build them: :func:`~rmspec.ocr.availability.require_backends`
and the :class:`~rmspec.ocr.availability.OcrEngine` vocabulary its one argument is written in.
Availability is not a port error, so the check is a name here rather than an ``except`` inside an
adapter; ``OcrEngine`` is exported with it because a function whose argument type is unreachable
is a function no composition root can call.

Everything that knows a wire format or a native bridge is private. :mod:`rmspec.ocr._bedrock`
owns the ``boto3`` client and the translation of every ``ClientError`` into the five model
errors; :mod:`rmspec.ocr._openai_wire` owns the Chat Completions envelope and its
``WireFormatError``; :mod:`rmspec.ocr._confidence` owns the character-weighted fold both
recognizers share; :mod:`rmspec.ocr._vision_framework` owns the only ``Vision`` and ``Quartz``
imports in this workspace. None of their names is re-exported, and the last one is not even
*reachable* from this module -- see below.

The adapter modules stay public and are deliberately not re-exported, exactly as
:mod:`rmspec.device.addresses` is: a reader who wants
:data:`~rmspec.ocr.availability.EXTRA`, :data:`~rmspec.ocr.textract.PROVIDER`,
:data:`~rmspec.ocr.apple_vision.DEFAULT_REVISION` or
:data:`~rmspec.ocr.vision_model.ADAPTER_REVISION` imports the module that owns it. Keeping those
constants out of this ``__all__`` is what lets every name in it be something a composition root
either binds to a port or calls to find out whether it may.

The client factories are public, and only one of them needed an entry
--------------------------------------------------------------------
A composition root must be able to obtain a ``bedrock-runtime`` client, a Textract client and a
Vision reader without importing ``boto3`` or ``pyobjc`` itself -- ``rmspec-ocr`` is the one
package the workspace's dependency map lets speak either, and both the lint layer's
``banned-api`` and ``tests/architecture/test_dependency_direction.py`` fail the build if
``rmspec.cli`` reaches for them. So all three factories are public. Two of them needed no name
here:

:meth:`~rmspec.ocr.textract.TextractRecognizer.in_region` and
:meth:`~rmspec.ocr.apple_vision.AppleVisionRecognizer.on_this_machine` are classmethods on
classes this module already exports, so they arrive with their owners and there is no
module-level name to list. That is not an accident of syntax: an engine whose construction and
whose reading are one class is an engine a container binds in one expression.

:func:`build_client` is the exception, and it is here because
:class:`~rmspec.ocr.vision_model.BedrockOpenAiVisionModel` deliberately has no such classmethod.
Its client is injected precisely so the entire suite drives it with a three-line stub and never
constructs an AWS client, which means the factory has no exported owner to arrive with -- and its
home, :mod:`rmspec.ocr._bedrock`, is private and stays private, because it is also where every
botocore exception is caught. This module is therefore the factory's only public address.

That widens the export rule from "a port binding" to "a port binding, or something a container
needs to build one" -- which is the rule :mod:`rmspec.device` already follows when it exports
:class:`~rmspec.device.usb.UsbWebApi` and :class:`~rmspec.device._shell.ParamikoShell`, two
transports that bind no port either. What it does not widen to is a private module's whole
surface: ``translated``, ``invoke``, ``endpoint_for`` and the five code sets stay where they are.

``import rmspec.ocr`` must not need the ``vision`` extra
-------------------------------------------------------
:mod:`rmspec.ocr._vision_framework` imports ``Vision`` and ``Quartz`` at module scope, so
importing it fails outright on Linux and on any macOS machine that synced without the extra.
Nothing reachable from this module imports it. The two names that use it --
:meth:`~rmspec.ocr.apple_vision.AppleVisionRecognizer.on_this_machine` and
:func:`~rmspec.ocr.availability.require_backends` -- both load it *by name* inside the call that
needs it, which is what lets the second one report a missing binding rather than merely be one.

``test_ocr_public_surface.py`` asserts that as a property of the import graph rather than of this
paragraph: it walks every module-scope import reachable from this file and from
:mod:`rmspec.ocr.testing`, and fails if the transitive closure contains the framework module.
A static walk rather than a ``sys.modules`` check, because this suite runs under
``pytest-randomly`` and ``pytest-xdist`` and another test legitimately imports that module on a
machine that can.

The doubles ship, and are never re-exported
-------------------------------------------
:mod:`rmspec.ocr.testing` holds the in-memory doubles for both ports. They ship under ``src/``
because later application-layer tests bind them, and they are imported explicitly and never
re-exported here -- a double bound in production would fabricate an answer for every page and
report it as a reading, under a cache key that gives no hint anything went wrong.

Two absences, both decided upstream
-----------------------------------
``RecognizerEnsemble``
    There is no ensemble adapter, and the reason is in ``ports/ocr.py``'s own docstring: fan-out
    width, ordering and partial-failure tolerance are use-case policy, not a technology axis.
    The app takes ``list[TextRecognizer]`` and loops, collecting per-recognizer failures, raising
    ``AllRecognizersFailed`` only when every recognizer failed, and folding the surviving
    ``provider_id`` values into its cache key. A port whose whole justification is "the legacy
    code used a ``ThreadPoolExecutor`` here" would also have meant a test-mode-only sequential
    adapter, so production would run the one path no use-case test exercises.

``MermaidSyntaxChecker`` / ``MermaidLinter`` / ``MermaidValidator``
    Three were proposed and all three were dropped, and their absence is a stronger statement
    than "not yet". Mermaid validity is a Node toolchain -- ``mmdc`` plus headless Chromium --
    and no Python extra can supply an npm binary, so a missing validator could not be expressed
    as the "missing package, install this extra" composition failure this architecture requires
    of every optional dependency. :func:`~rmspec.ocr.availability.require_backends` is what that
    failure looks like when it *can* be expressed, and there is no honest way to make it cover a
    binary ``uv sync`` has never heard of. The one idea worth keeping from those proposals --
    that "I did not actually check this" must be a representable value rather than a
    keyword-prefix match pretending to be a parse -- belongs on the diagram value object in the
    diagram slice.
"""

from __future__ import annotations

from rmspec.ocr._bedrock import build_client
from rmspec.ocr.apple_vision import AppleVisionRecognizer
from rmspec.ocr.availability import OcrEngine, require_backends
from rmspec.ocr.textract import TextractRecognizer
from rmspec.ocr.vision_model import BedrockOpenAiVisionModel

__all__ = [
    "AppleVisionRecognizer",
    "BedrockOpenAiVisionModel",
    "OcrEngine",
    "TextractRecognizer",
    "build_client",
    "require_backends",
]
