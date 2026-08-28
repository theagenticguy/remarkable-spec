"""No credential from the device config may be readable, logged, or committed.

Background: ``/home/root/.config/remarkable/xochitl.conf`` on firmware 3.27.3.0
holds ``DeveloperPassword`` in cleartext plus two JWTs (``UserToken``,
``devicetoken``). Reading that file during development leaked all three into a
session transcript once already. These tests make the same mistake fail the
build instead.

The rule is not "handle the secrets carefully". It is that no code path in this
workspace reads that file at all, and no literal value from it is ever committed.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Config keys whose values are credentials. A source file naming one of these
#: is presumed to be reading it unless it is this test.
SECRET_KEYS = ("DeveloperPassword", "UserToken", "devicetoken")

#: The secret-bearing file itself. No adapter may reference it.
FORBIDDEN_DEVICE_PATHS = ("xochitl.conf",)

#: Shapes that mean "somebody pasted a real credential into the tree".
CREDENTIAL_SHAPES = (
    # A JWT: three base64url segments. The header segment of an unencrypted JWT
    # is effectively constant, which makes this cheap and precise.
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    # An AWS access key id.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # A PEM private key block.
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _tracked_text_files() -> list[pathlib.Path]:
    """Every git-tracked file, minus this test and binary blobs.

    Uses ``git ls-files`` rather than a filesystem walk because trackedness is
    the property that matters -- an untracked scratch file cannot leak, and
    ``git check-ignore`` alone is unreliable here (``.codegraph/`` and
    ``.pytest_cache/`` are untracked but not ignored).
    """
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(REPO), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    files: list[pathlib.Path] = []
    for rel in out.stdout.split("\0"):
        if not rel:
            continue
        path = REPO / rel
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue
        if not path.is_file():
            continue
        if path.suffix in {".png", ".jpg", ".jpeg", ".pdf", ".rm", ".whl", ".gz"}:
            continue
        files.append(path)
    return files


@pytest.mark.parametrize("shape", CREDENTIAL_SHAPES, ids=lambda s: s.pattern[:24])
def test_no_committed_credential_shapes(shape: re.Pattern[str]) -> None:
    hits: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if shape.search(line):
                hits.append(f"  {path.relative_to(REPO)}:{lineno}")
    assert not hits, (
        f"credential-shaped literal matching /{shape.pattern}/ found at:\n" + "\n".join(hits)
    )


def test_no_source_file_reads_the_device_config() -> None:
    """No adapter may open the secret-bearing config, even to parse one key from it.

    The device exposes everything this project needs without it: identity comes
    from ``/etc/os-release`` and ``/sys/devices/soc0/machine``, and SSH auth comes
    from the user's own key. There is no legitimate reason to open
    ``xochitl.conf``, so the check is absence, not redaction.
    """
    needles = (*FORBIDDEN_DEVICE_PATHS, *SECRET_KEYS)
    hits = [
        f"  {path.relative_to(REPO)}:{lineno}: {needle}"
        for path in sorted((REPO / "packages").rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for needle in needles
        if needle in line
    ]
    assert not hits, (
        "source referenced the secret-bearing device config or one of its credential keys:\n"
        + "\n".join(hits)
    )


def test_gitignore_covers_the_generated_and_secret_prone_paths() -> None:
    """The paths that must never be committable, asserted against git itself."""
    must_ignore = ("docs/.repomix/codebase.json", ".venv/x", "dist/x", ".env")
    for rel in must_ignore:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(REPO), "check-ignore", "-q", rel],  # noqa: S607
            check=False,
        )
        assert result.returncode == 0, f"{rel} is not gitignored"
