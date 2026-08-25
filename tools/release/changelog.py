"""Read and rewrite CHANGELOG.md.

The changelog is the source of the GitHub Release body, so extraction has to be exact:
a release must never ship notes belonging to a different version.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .version import Version, VersionError

UNRELEASED_HEADING = "## [Unreleased]"
# Matches '## [1.2.3] - 2026-08-25' and the Unreleased heading alike.
HEADING_PATTERN = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*-\s*(?P<date>\S+))?\s*$", re.MULTILINE)


class ChangelogError(ValueError):
    """Raised when the changelog is missing a required section."""


def extract_notes(text: str, version: str) -> str:
    """Return the body of one version's section, without its heading."""
    headings = list(HEADING_PATTERN.finditer(text))
    for index, heading in enumerate(headings):
        if heading["version"] != version:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[start:end]
        # Trailing link-reference definitions belong to the document, not the section,
        # and the last section carries all of them.
        body = re.sub(r"(?:\n\[[^\]]+\]:[^\n]*)+\s*$", "", body.rstrip())
        return body.strip()
    raise ChangelogError(f"CHANGELOG.md has no section for version {version}.")


def release_unreleased(text: str, version: Version, today: date, repo: str) -> str:
    """Convert the Unreleased section into a dated release section.

    Also rewrites the link-reference definitions so `Unreleased` compares against the new
    tag and the new version links to its release page.
    """
    if UNRELEASED_HEADING not in text:
        raise ChangelogError("CHANGELOG.md has no '## [Unreleased]' section to release.")
    if extract_notes(text, "Unreleased") == "":
        raise ChangelogError(
            "The Unreleased section is empty. Describe the change before cutting a release."
        )

    updated = text.replace(
        UNRELEASED_HEADING,
        f"{UNRELEASED_HEADING}\n\n## [{version}] - {today.isoformat()}",
        1,
    )

    previous = _previous_version(text)
    compare_base = f"v{previous}" if previous else "v" + str(version)
    unreleased_link = f"[Unreleased]: https://github.com/{repo}/compare/v{version}...HEAD"
    version_link = (
        f"[{version}]: https://github.com/{repo}/compare/{compare_base}...v{version}"
        if previous
        else f"[{version}]: https://github.com/{repo}/releases/tag/v{version}"
    )

    updated = re.sub(r"^\[Unreleased\]:.*$", unreleased_link, updated, count=1, flags=re.MULTILINE)
    if unreleased_link in updated:
        # Keep the reference block newest-first by slotting the new link straight after
        # Unreleased rather than appending it below older releases.
        return updated.replace(unreleased_link, f"{unreleased_link}\n{version_link}", 1).rstrip() + "\n"
    return updated.rstrip() + f"\n{unreleased_link}\n{version_link}\n"


def _previous_version(text: str) -> str | None:
    for heading in HEADING_PATTERN.finditer(text):
        if heading["version"] == "Unreleased":
            continue
        return heading["version"]
    return None


def notes_for_tag(changelog: Path, version: Version) -> str:
    try:
        return extract_notes(changelog.read_text(encoding="utf-8"), str(version))
    except ChangelogError as exc:
        raise VersionError(str(exc)) from exc
