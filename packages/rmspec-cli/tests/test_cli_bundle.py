"""The single-distribution build tree: that it is derived from the nine, and refuses to guess."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from rmspec.cli._bundle import (
    BUNDLE_NAME,
    COPIED_FILES,
    ENTRY_POINT_MEMBER,
    NAMESPACE,
    STAGE_TASK,
    BundleError,
    Member,
    main,
    read_members,
    render_pyproject,
    requirement_name,
    stage,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

REPO = Path(__file__).resolve().parents[3]
PACKAGES = REPO / "packages"


# ─────────────────────────── a synthetic workspace ───────────────────────────


def _write_member(
    workspace: Path,
    distribution: str,
    module: str,
    *,
    version: str = "0.2.0",
    dependencies: Iterable[str] = (),
    extras: Mapping[str, Iterable[str]] | None = None,
    scripts: Mapping[str, str] | None = None,
    with_source: bool = True,
) -> None:
    """Write one plausible member manifest, and optionally the module it claims to ship."""
    root = workspace / "packages" / distribution
    root.mkdir(parents=True)
    if with_source:
        source = root.joinpath("src", *module.split("."))
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("", encoding="utf-8")
        (source / "py.typed").write_text("", encoding="utf-8")
    lines = [
        "[project]",
        f"name = {json.dumps(distribution)}",
        f"version = {json.dumps(version)}",
        'description = "a member"',
        'license = "MIT"',
        'requires-python = ">=3.13"',
        'authors = [{ name = "A Person", email = "person@example.com" }]',
        "dependencies = [",
        *(f"    {json.dumps(spec)}," for spec in dependencies),
        "]",
        "",
        "[project.optional-dependencies]",
        *(
            line
            for name, specs in (extras or {}).items()
            for line in (f"{name} = [", *(f"    {json.dumps(s)}," for s in specs), "]")
        ),
        "",
        "[project.scripts]",
        *(f"{name} = {json.dumps(target)}" for name, target in (scripts or {}).items()),
        "",
        "[build-system]",
        'requires = ["uv_build>=0.11.12,<0.12.0"]',
        'build-backend = "uv_build"',
        "",
        "[tool.uv.build-backend]",
        f"module-name = {json.dumps(module)}",
        "namespace = true",
        "",
    ]
    (root / "pyproject.toml").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(name="fake")
def _fake(tmp_path: Path) -> Path:
    """Build a two-member workspace that bundles cleanly, for the error paths to deviate from."""
    _write_member(
        tmp_path,
        "rmspec-domain",
        "rmspec.domain",
        dependencies=["pydantic>=2.12.5"],
    )
    _write_member(
        tmp_path,
        ENTRY_POINT_MEMBER,
        "rmspec.cli",
        dependencies=["rmspec-domain", "cyclopts>=4.6.0", "pydantic>=2.12.5"],
        extras={"push": ["markdown>=3.10.2"]},
        scripts={"rmspec": "rmspec.cli:app"},
    )
    for name in COPIED_FILES:
        (tmp_path / name).write_text(f"{name} body\n", encoding="utf-8")
    return tmp_path


# ─────────────────────────── reading the members ───────────────────────────


def test_the_real_workspace_reads_as_nine_members_under_one_namespace():
    members = read_members(REPO)

    assert len(members) == 9
    assert [m.module for m in members] == [
        f"{NAMESPACE}.{name}"
        for name in (
            "app",
            "cli",
            "device",
            "domain",
            "export",
            "formats",
            "ocr",
            "persistence",
            "render",
        )
    ]
    assert all(m.source.is_dir() for m in members)


def test_a_members_own_workspace_requirements_are_dropped_because_they_become_contents():
    members = {m.distribution: m for m in read_members(REPO)}
    cli = members[ENTRY_POINT_MEMBER]

    declared = tomllib.loads(
        (PACKAGES / ENTRY_POINT_MEMBER / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert any(s.startswith("rmspec-") for s in declared["project"]["dependencies"])
    assert not any(s.startswith("rmspec-") for s in cli.requirements)
    assert "cyclopts>=4.6.0" in cli.requirements


def test_a_module_outside_the_namespace_is_refused_rather_than_placed_somewhere(tmp_path: Path):
    _write_member(tmp_path, "other", "somethingelse.core")

    with pytest.raises(BundleError, match="outside the 'rmspec' namespace"):
        read_members(tmp_path)


def test_a_module_name_with_no_directory_behind_it_is_refused(tmp_path: Path):
    _write_member(tmp_path, "rmspec-ghost", "rmspec.ghost", with_source=False)

    with pytest.raises(BundleError, match="does not exist"):
        read_members(tmp_path)


def test_an_empty_workspace_is_refused_rather_than_bundled_into_an_empty_wheel(tmp_path: Path):
    with pytest.raises(BundleError, match="no member manifests"):
        read_members(tmp_path)


# ─────────────────────────── the requirement algebra ───────────────────────────


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("pydantic>=2.12.5", "pydantic"),
        ("rmscene>=0.7.0,<0.8.0", "rmscene"),
        ("pyobjc-framework-vision>=12.1; sys_platform == 'darwin'", "pyobjc-framework-vision"),
        ("uv_build>=0.11.12,<0.12.0", "uv-build"),
        ("httpx[http2]>=0.28.1", "httpx"),
        ("Pillow", "pillow"),
        ("ty===0.0.66", "ty"),
        ("pytest!=9.0.0", "pytest"),
        ("hypothesis~=6.165", "hypothesis"),
        ("diff-cover<11", "diff-cover"),
    ],
)
def test_a_requirement_specifier_reduces_to_its_normalised_name(spec: str, expected: str):
    assert requirement_name(spec) == expected


def test_two_members_asking_for_one_requirement_differently_is_an_error_not_a_choice(
    tmp_path: Path,
):
    _write_member(tmp_path, "rmspec-domain", "rmspec.domain", dependencies=["pydantic>=2.12.5"])
    _write_member(
        tmp_path,
        ENTRY_POINT_MEMBER,
        "rmspec.cli",
        dependencies=["pydantic>=2.13.0"],
        scripts={"rmspec": "rmspec.cli:app"},
    )

    with pytest.raises(BundleError, match="One wheel carries one specifier"):
        render_pyproject(read_members(tmp_path))


def test_members_that_disagree_about_the_version_leave_the_bundle_none_to_claim(tmp_path: Path):
    _write_member(tmp_path, "rmspec-domain", "rmspec.domain", version="0.3.0")
    _write_member(
        tmp_path,
        ENTRY_POINT_MEMBER,
        "rmspec.cli",
        version="0.2.0",
        scripts={"rmspec": "rmspec.cli:app"},
    )

    with pytest.raises(BundleError, match="disagree about the version"):
        render_pyproject(read_members(tmp_path))


def test_without_the_entry_point_member_there_is_nothing_to_ship(tmp_path: Path):
    _write_member(tmp_path, "rmspec-domain", "rmspec.domain")

    with pytest.raises(BundleError, match=ENTRY_POINT_MEMBER):
        render_pyproject(read_members(tmp_path))


# ─────────────────────────── the rendered manifest ───────────────────────────


@pytest.fixture(name="rendered")
def _rendered() -> dict[str, Any]:
    return tomllib.loads(render_pyproject(read_members(REPO)))


def test_the_rendered_manifest_is_valid_toml_naming_the_bundle(rendered: dict[str, Any]):
    assert rendered["project"]["name"] == BUNDLE_NAME
    assert rendered["project"]["readme"] == COPIED_FILES[0]
    assert rendered["project"]["description"]


def test_the_rendered_manifest_requires_nothing_from_this_workspace(rendered: dict[str, Any]):
    # This is the whole reason the bundle exists: rmspec-cli's own wheel requires eight
    # distributions that exist on no index, so it cannot be installed anywhere but here.
    assert not [
        spec
        for spec in rendered["project"]["dependencies"]
        if requirement_name(spec).startswith("rmspec-")
    ]


def test_the_rendered_manifest_carries_every_third_party_requirement_of_every_member(
    rendered: dict[str, Any],
):
    expected: set[str] = set()
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        expected.update(
            spec
            for spec in project.get("dependencies", [])
            if not requirement_name(spec).startswith("rmspec-")
        )

    assert expected
    assert expected == set(rendered["project"]["dependencies"])


def test_the_rendered_manifest_carries_every_extra_of_every_member(rendered: dict[str, Any]):
    expected: dict[str, set[str]] = {}
    for pyproject in sorted(PACKAGES.glob("*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        for name, specs in (project.get("optional-dependencies") or {}).items():
            expected.setdefault(name, set()).update(specs)

    assert set(expected) == {"push", "vision"}
    assert {
        name: set(specs) for name, specs in rendered["project"]["optional-dependencies"].items()
    } == expected


def test_the_bundle_inherits_metadata_from_the_entry_point_member_rather_than_restating_it(
    rendered: dict[str, Any],
):
    inherited = tomllib.loads(
        (PACKAGES / ENTRY_POINT_MEMBER / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert rendered["project"]["scripts"] == inherited["project"]["scripts"]
    assert rendered["project"]["requires-python"] == inherited["project"]["requires-python"]
    assert rendered["project"]["license"] == inherited["project"]["license"]
    assert rendered["project"]["authors"] == inherited["project"]["authors"]
    assert rendered["build-system"] == inherited["build-system"]


def test_the_bundle_claims_the_one_version_the_members_share(rendered: dict[str, Any]):
    versions = {m.version for m in read_members(REPO)}

    assert len(versions) == 1
    assert rendered["project"]["version"] == versions.pop()


def test_the_manifest_stays_a_pep_420_namespace_so_the_subpackages_remain_siblings(
    rendered: dict[str, Any],
):
    backend = rendered["tool"]["uv"]["build-backend"]

    assert backend == {"module-name": NAMESPACE, "namespace": True}


def test_the_manifest_says_it_is_generated_and_names_the_task_that_regenerates_it():
    document = render_pyproject(read_members(REPO))

    assert document.splitlines()[0].startswith("#")
    assert STAGE_TASK in document.splitlines()[0]
    assert document.endswith("\n")
    assert not document.endswith("\n\n")


def test_a_quoted_string_survives_the_characters_toml_would_otherwise_swallow(tmp_path: Path):
    _write_member(tmp_path, "rmspec-domain", "rmspec.domain", dependencies=['weird"\\pkg'])
    _write_member(
        tmp_path,
        ENTRY_POINT_MEMBER,
        "rmspec.cli",
        scripts={"rmspec": "rmspec.cli:app"},
    )

    rendered = tomllib.loads(render_pyproject(read_members(tmp_path)))

    assert rendered["project"]["dependencies"] == ['weird"\\pkg']


# ─────────────────────────── staging the tree ───────────────────────────


def test_staging_writes_a_buildable_tree_holding_every_members_subpackage(fake: Path):
    members = stage(fake, fake / "build" / "bundle")
    destination = fake / "build" / "bundle"

    assert [m.distribution for m in members] == [ENTRY_POINT_MEMBER, "rmspec-domain"]
    assert (destination / "pyproject.toml").is_file()
    assert sorted(p.name for p in (destination / "src" / NAMESPACE).iterdir()) == [
        "cli",
        "domain",
    ]
    for name in COPIED_FILES:
        assert (destination / name).read_text(encoding="utf-8") == f"{name} body\n"


def test_staging_never_writes_a_namespace_init_which_would_shadow_eight_subpackages(fake: Path):
    destination = fake / "build" / "bundle"
    stage(fake, destination)

    assert not (destination / "src" / NAMESPACE / "__init__.py").exists()


def test_staging_ships_the_pep_561_marker_of_every_member(fake: Path):
    destination = fake / "build" / "bundle"
    members = stage(fake, destination)

    for member in members:
        placed = destination.joinpath("src", *member.module.split("."), "py.typed")
        assert placed.is_file(), f"{member.distribution} lost its py.typed in the copy"


def test_staging_replaces_the_tree_so_a_deleted_module_cannot_survive_in_the_wheel(fake: Path):
    destination = fake / "build" / "bundle"
    stage(fake, destination)
    stale = destination / "src" / NAMESPACE / "ghost"
    stale.mkdir()
    (stale / "__init__.py").write_text("", encoding="utf-8")

    stage(fake, destination)

    assert not stale.exists()


def test_staging_does_not_copy_bytecode_caches_into_the_distribution(fake: Path):
    cached = fake / "packages" / "rmspec-domain" / "src" / "rmspec" / "domain" / "__pycache__"
    cached.mkdir()
    (cached / "__init__.cpython-313.pyc").write_bytes(b"\x00")
    destination = fake / "build" / "bundle"

    stage(fake, destination)

    assert not list(destination.rglob("__pycache__"))


def test_the_real_workspace_stages_into_a_tree_whose_manifest_matches_what_it_renders(
    tmp_path: Path,
):
    destination = tmp_path / "bundle"
    members = stage(REPO, destination)

    assert (destination / "pyproject.toml").read_text(encoding="utf-8") == render_pyproject(
        members
    )
    assert len(list((destination / "src" / NAMESPACE).iterdir())) == 9


# ─────────────────────────── the command ───────────────────────────


def test_the_command_stages_the_tree_and_reports_what_it_staged(
    fake: Path,
    capsys: pytest.CaptureFixture[str],
):
    code = main([str(fake), str(fake / "out")])

    assert code == 0
    assert (fake / "out" / "pyproject.toml").is_file()
    captured = capsys.readouterr()
    assert captured.out == "", "stdout is the machine's; this tool has no payload for it"
    assert "rmspec.cli" in captured.err


def test_the_command_reports_a_bundling_failure_on_stderr_and_exits_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    code = main([str(tmp_path), str(tmp_path / "out")])

    assert code == 1
    assert "no member manifests" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# ─────────────────────────── the type it hands back ───────────────────────────


def test_every_member_is_read_from_its_own_directory_and_no_siblings():
    # The same property test_packages_importable asserts of the installed namespace: a
    # module-name that resolved into another member's tree would stage that member twice and
    # drop this one, and the wheel would still look plausible.
    for member in read_members(REPO):
        assert isinstance(member, Member)
        assert member.source == PACKAGES.joinpath(
            member.distribution, "src", *member.module.split(".")
        )
