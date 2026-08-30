"""The composition root's backend probe, including every negative branch.

The loader is injected, so both outcomes are asserted on either platform: a machine with the
``vision`` extra installed proves the real import and the real probe, and a machine without it
proves the message that names the missing binding. One test covers the real default loader on
both, by branching on what is actually installed rather than by skipping.
"""

from __future__ import annotations

from importlib.util import find_spec
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from rmspec.domain.errors import MissingDependencyError, OcrError, RmspecError
from rmspec.ocr import apple_vision, textract
from rmspec.ocr.availability import EXTRA, FEATURE, OcrEngine, require_backends

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

BOTH = [OcrEngine.APPLE_VISION, OcrEngine.AWS_TEXTRACT]


def loader_returning(probe: Callable[[], int]) -> Callable[[str], ModuleType]:
    """Build a loader that hands back a module-shaped object with that probe."""

    def load(_name: str) -> ModuleType:
        return cast("ModuleType", SimpleNamespace(probe_backend=probe))

    return load


def loader_raising(error: Exception) -> Callable[[str], ModuleType]:
    """Build a loader that fails the way a missing binding does."""

    def load(_name: str) -> ModuleType:
        raise error

    return load


def loader_that_must_not_run(name: str) -> ModuleType:
    """Fail if the vision backend is probed at all."""
    pytest.fail(f"the vision backend must not be loaded, and was asked for {name!r}")


def working() -> Callable[[str], ModuleType]:
    """Build a loader whose probe reads the blank probe image and finds no text."""
    return loader_returning(lambda: 0)


def test_the_extra_named_is_the_one_a_user_can_install() -> None:
    assert EXTRA == "vision"
    assert FEATURE


def test_the_engine_names_are_the_recognizers_own_slugs() -> None:
    # So a reader can match a composition's engine list against the provider_id the app folds
    # into its cache key, with no translation table between the two.
    assert OcrEngine.APPLE_VISION == apple_vision.PROVIDER
    assert OcrEngine.AWS_TEXTRACT == textract.PROVIDER


def test_a_textract_only_composition_is_never_told_to_install_pyobjc() -> None:
    # The whole reason this function takes an engine list. boto3 is a hard dependency of this
    # distribution and has no extra to name; pyobjc is an optional, platform-gated one.
    require_backends([OcrEngine.AWS_TEXTRACT], load=loader_that_must_not_run)


def test_an_empty_composition_checks_nothing() -> None:
    require_backends([], load=loader_that_must_not_run)


def test_a_working_backend_passes() -> None:
    require_backends(BOTH, load=working())


@pytest.mark.parametrize("package", ["Vision", "Quartz"])
def test_a_missing_binding_names_that_binding_and_its_extra(package: str) -> None:
    missing = ModuleNotFoundError(f"No module named {package!r}", name=package)
    with pytest.raises(MissingDependencyError) as caught:
        require_backends(BOTH, load=loader_raising(missing))
    assert caught.value.package == package
    assert caught.value.extra == "vision"
    assert caught.value.feature == FEATURE
    assert "uv sync --extra vision" in (caught.value.remediation or "")
    assert caught.value.__cause__ is missing


def test_an_import_failure_that_names_no_module_still_names_a_package() -> None:
    with pytest.raises(MissingDependencyError) as caught:
        require_backends([OcrEngine.APPLE_VISION], load=loader_raising(ImportError("dlopen")))
    assert caught.value.package == "Vision"


def test_bindings_that_import_but_cannot_read_are_the_same_missing_dependency() -> None:
    # The shape of a real failure: the wheels install on any macOS, and the framework bundle
    # or its model can still be unavailable. The user's next action is the same either way.
    def unusable() -> int:
        msg = "vision.framework: the Vision request failed"
        raise RuntimeError(msg)

    with pytest.raises(MissingDependencyError) as caught:
        require_backends([OcrEngine.APPLE_VISION], load=loader_returning(unusable))
    assert caught.value.package == "Vision"
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_the_failure_cannot_be_swallowed_by_a_use_case_catching_ocr_errors() -> None:
    # Availability is not a port error: a use case writing `except OcrError` must not be able
    # to swallow a wiring bug, so the error is a direct child of the root.
    with pytest.raises(MissingDependencyError) as caught:
        require_backends(BOTH, load=loader_raising(ImportError("dlopen")))
    assert isinstance(caught.value, RmspecError)
    assert not isinstance(caught.value, OcrError)


def test_the_default_loader_is_the_real_framework_module() -> None:
    # No skip: both platforms assert the truth about themselves. On macOS with the extra this
    # imports the bindings and runs a real recognition, and passing is the assertion. Anywhere
    # else it is the message a user gets, which is the branch a macOS developer machine would
    # otherwise never exercise.
    if find_spec("Vision") is None:
        with pytest.raises(MissingDependencyError) as caught:
            require_backends([OcrEngine.APPLE_VISION])
        assert caught.value.extra == "vision"
        assert caught.value.package in {"Vision", "Quartz"}
    else:
        require_backends([OcrEngine.APPLE_VISION])
