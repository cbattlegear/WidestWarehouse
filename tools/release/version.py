"""Semantic version parsing and the loader's version file.

Kept deliberately small and dependency-free: the publish workflow runs this before
anything is built, so it must work with nothing but a bare Python install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The official SemVer 2.0.0 pattern, anchored. Pre-release and build metadata are
# accepted so a release candidate such as 1.2.0-rc.1 is a legal version.
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

VERSION_ASSIGNMENT = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)


class VersionError(ValueError):
    """Raised when a version string or tag is not usable as a release."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None
    build: str | None = None

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += f"-{self.prerelease}"
        if self.build:
            text += f"+{self.build}"
        return text

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None

    def bump(self, part: str) -> "Version":
        """Return the next version. Bumping always clears pre-release and build metadata."""
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise VersionError(f"Unknown version part '{part}'. Expected major, minor, or patch.")


def parse_version(text: str) -> Version:
    match = SEMVER_PATTERN.match(text.strip())
    if not match:
        raise VersionError(f"'{text}' is not a valid semantic version (expected MAJOR.MINOR.PATCH).")
    return Version(
        major=int(match["major"]),
        minor=int(match["minor"]),
        patch=int(match["patch"]),
        prerelease=match["prerelease"],
        build=match["build"],
    )


def parse_tag(tag: str) -> Version:
    """Parse a git tag such as `v1.4.0` or `refs/tags/v1.4.0`."""
    name = tag.strip()
    if name.startswith("refs/tags/"):
        name = name[len("refs/tags/") :]
    if not name.startswith("v"):
        raise VersionError(f"Release tag '{tag}' must start with 'v', for example v1.0.0.")
    return parse_version(name[1:])


def read_version(version_file: Path) -> Version:
    match = VERSION_ASSIGNMENT.search(version_file.read_text(encoding="utf-8"))
    if not match:
        raise VersionError(f"No __version__ assignment found in {version_file}.")
    return parse_version(match["version"])


def write_version(version_file: Path, version: Version) -> None:
    original = version_file.read_text(encoding="utf-8")
    updated, count = VERSION_ASSIGNMENT.subn(f'__version__ = "{version}"', original, count=1)
    if count != 1:
        raise VersionError(f"No __version__ assignment found in {version_file}.")
    version_file.write_text(updated, encoding="utf-8", newline="")
