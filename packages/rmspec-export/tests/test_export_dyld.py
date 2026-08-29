"""The macOS library-search policy, as a table plus a property. No native library is touched."""

from __future__ import annotations

import os
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rmspec.export._dyld import (
    DYLD_FALLBACK_VARIABLE,
    HOMEBREW_LIBRARY_DIRECTORIES,
    ensure_native_library_path,
    fallback_library_path,
)

CANDIDATES = ("/a/lib", "/b/lib")


@pytest.mark.parametrize(
    ("current", "platform", "present", "expected"),
    [
        (None, "darwin", {"/a/lib", "/b/lib"}, "/a/lib:/b/lib"),
        (None, "darwin", {"/b/lib"}, "/b/lib"),
        (None, "darwin", {"/a/lib"}, "/a/lib"),
        (None, "darwin", set(), None),
        ("/already/set", "darwin", {"/a/lib"}, None),
        ("", "darwin", {"/a/lib"}, "/a/lib"),
        (None, "linux", {"/a/lib"}, None),
        (None, "win32", {"/a/lib"}, None),
        ("/already/set", "linux", {"/a/lib"}, None),
    ],
)
def test_policy_table(
    current: str | None,
    platform: str,
    present: set[str],
    expected: str | None,
) -> None:
    assert (
        fallback_library_path(
            current,
            platform=platform,
            candidates=CANDIDATES,
            exists=present.__contains__,
        )
        == expected
    )


def test_an_existing_value_is_never_extended_or_reordered() -> None:
    # A launcher, a container or a developer may have set this deliberately, and appending to it
    # would silently change which libcairo a machine loads.
    assert (
        fallback_library_path(
            "/opt/custom/lib",
            platform="darwin",
            candidates=CANDIDATES,
            exists=lambda _: True,
        )
        is None
    )


def test_candidate_order_is_preserved_not_sorted() -> None:
    reversed_candidates = ("/z/lib", "/a/lib")
    assert (
        fallback_library_path(
            None,
            platform="darwin",
            candidates=reversed_candidates,
            exists=lambda _: True,
        )
        == "/z/lib:/a/lib"
    )


@given(
    current=st.one_of(st.none(), st.text()),
    platform=st.sampled_from(["darwin", "linux", "win32", "freebsd"]),
    present=st.sets(st.sampled_from(CANDIDATES)),
)
def test_result_only_ever_names_candidate_directories(
    current: str | None,
    platform: str,
    present: set[str],
) -> None:
    result = fallback_library_path(
        current,
        platform=platform,
        candidates=CANDIDATES,
        exists=present.__contains__,
    )
    if result is None:
        return
    assert set(result.split(os.pathsep)) <= set(CANDIDATES)
    assert set(result.split(os.pathsep)) == present


def test_the_default_candidates_are_the_two_homebrew_prefixes() -> None:
    assert HOMEBREW_LIBRARY_DIRECTORIES == ("/opt/homebrew/lib", "/usr/local/lib")


def test_ensure_leaves_an_already_set_variable_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DYLD_FALLBACK_VARIABLE, "/preset")
    assert ensure_native_library_path() is None
    assert os.environ[DYLD_FALLBACK_VARIABLE] == "/preset"


def test_ensure_writes_exactly_one_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DYLD_FALLBACK_VARIABLE, raising=False)
    before = dict(os.environ)
    written = ensure_native_library_path()
    changed = {
        key for key in set(before) | set(os.environ) if before.get(key) != os.environ.get(key)
    }
    if sys.platform == "darwin":
        assert written is not None
        assert changed == {DYLD_FALLBACK_VARIABLE}
    else:
        assert written is None
        assert changed == set()
