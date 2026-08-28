"""The coverage gate must be able to see every package's source.

This exists because of a real near-miss. `--cov`, `--cov=packages` and
`--cov=rmspec` all measure only the nine already-imported ``__init__.py`` files
-- 18 statements, reported as 100% -- because ``rmspec`` is a PEP 420 namespace
package with no single ``__file__`` for coverage to walk. The ">=90% coverage"
gate therefore passed while 6,269 lines of brand-new untested source sat beside
it. Only an explicit ``--cov=<module dir>`` per package sees them.

So the gate needs a guard of its own: adding a tenth package without adding its
``--cov`` flag would silently shrink what the floor applies to.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
MISE = REPO / "mise.toml"
PACKAGES = REPO / "packages"


def _cov_targets(task: str) -> set[str]:
    body = MISE.read_text()
    match = re.search(rf"\[tasks\.{re.escape(task)}\].*?\nrun = \"(.*?)\"\n", body, re.DOTALL)
    assert match, f"no [tasks.{task}] with a run line in mise.toml"
    return set(re.findall(r"--cov=(\S+)", match.group(1)))


def _expected_targets() -> set[str]:
    out = set()
    for pkg in sorted(PACKAGES.iterdir()):
        if not pkg.is_dir():
            continue
        name = pkg.name.removeprefix("rmspec-")
        out.add(f"packages/{pkg.name}/src/rmspec/{name}")
    return out


def test_test_cov_task_measures_every_package() -> None:
    expected = _expected_targets()
    actual = _cov_targets("test-cov")
    missing = sorted(expected - actual)
    assert not missing, (
        "mise task `test-cov` does not measure these packages, so the >=90% floor "
        f"does not apply to them: {missing}"
    )


def test_test_task_measures_every_package() -> None:
    missing = sorted(_expected_targets() - _cov_targets("test"))
    assert not missing, f"mise task `test` does not measure: {missing}"


def test_no_bare_cov_flag_anywhere_in_mise() -> None:
    """A bare `--cov` silently narrows measurement to imported files only."""
    # Scan `run = ` lines only. The explanatory comment above [tasks.test-cov]
    # quotes a bare `--cov` on purpose, and tripping on prose would make this
    # guard unmaintainable.
    offenders = [
        line.strip()
        for line in MISE.read_text().splitlines()
        if line.lstrip().startswith("run =") and re.search(r"--cov(?![=\w-])", line)
    ]
    assert not offenders, (
        "bare `--cov` (no `=target`) found in mise.toml; it measures only "
        f"already-imported files: {offenders}"
    )


def test_coverage_floor_is_at_least_90() -> None:
    body = MISE.read_text()
    floors = [int(m) for m in re.findall(r"--cov-fail-under=(\d+)", body)]
    assert floors, "no --cov-fail-under anywhere in mise.toml"
    assert min(floors) >= 90, f"coverage floor was lowered to {min(floors)}"
