from datetime import date

import pytest

from tools.release.changelog import ChangelogError, extract_notes, release_unreleased
from tools.release.version import (
    VersionError,
    Version,
    parse_tag,
    parse_version,
    read_version,
    write_version,
)

REPO = "cbattlegear/WidestWarehouse"


def test_parses_plain_and_prerelease_versions():
    assert str(parse_version("1.2.3")) == "1.2.3"
    parsed = parse_version("2.0.0-rc.1")
    assert parsed.is_prerelease
    assert str(parsed) == "2.0.0-rc.1"


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3.4", "01.2.3", "", "next"])
def test_rejects_invalid_versions(bad):
    with pytest.raises(VersionError):
        parse_version(bad)


def test_parses_tags_including_full_ref():
    assert str(parse_tag("v1.4.0")) == "1.4.0"
    assert str(parse_tag("refs/tags/v1.4.0")) == "1.4.0"


def test_tag_without_v_prefix_is_rejected():
    with pytest.raises(VersionError):
        parse_tag("1.4.0")


def test_bump_resets_lower_parts_and_prerelease():
    version = Version(1, 4, 7, prerelease="rc.2")
    assert str(version.bump("major")) == "2.0.0"
    assert str(version.bump("minor")) == "1.5.0"
    assert str(version.bump("patch")) == "1.4.8"


def test_bump_rejects_unknown_part():
    with pytest.raises(VersionError):
        Version(1, 0, 0).bump("build")


def test_round_trips_the_version_file(tmp_path):
    path = tmp_path / "version.py"
    path.write_text('"""doc."""\n\n__version__ = "1.0.0"\n\nOTHER = 1\n', encoding="utf-8")
    assert str(read_version(path)) == "1.0.0"
    write_version(path, parse_version("1.1.0"))
    assert str(read_version(path)) == "1.1.0"
    # Everything around the assignment must survive untouched.
    assert "OTHER = 1" in path.read_text(encoding="utf-8")


def test_the_real_version_file_is_valid_semver():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    version = read_version(repo_root / "loader" / "app" / "version.py")
    assert version.major >= 1


def test_the_real_changelog_documents_the_current_version():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    version = read_version(repo_root / "loader" / "app" / "version.py")
    notes = extract_notes((repo_root / "CHANGELOG.md").read_text(encoding="utf-8"), str(version))
    assert notes.strip(), f"CHANGELOG.md has no notes for {version}"


CHANGELOG = """# Changelog

## [Unreleased]

### Added

- A new job.

## [1.0.0] - 2026-08-25

### Added

- The first release.

[Unreleased]: https://github.com/cbattlegear/WidestWarehouse/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cbattlegear/WidestWarehouse/releases/tag/v1.0.0
"""


def test_extracts_only_the_requested_section():
    notes = extract_notes(CHANGELOG, "1.0.0")
    assert "The first release." in notes
    assert "A new job." not in notes
    # Link-reference definitions are document furniture, not release notes.
    assert "https://github.com" not in notes


def test_extract_notes_rejects_an_unknown_version():
    with pytest.raises(ChangelogError):
        extract_notes(CHANGELOG, "9.9.9")


def test_releasing_unreleased_dates_a_new_section():
    updated = release_unreleased(CHANGELOG, parse_version("1.1.0"), date(2026, 9, 1), REPO)
    assert "## [1.1.0] - 2026-09-01" in updated
    # The moved notes now belong to 1.1.0 and Unreleased is empty again.
    assert "A new job." in extract_notes(updated, "1.1.0")
    assert extract_notes(updated, "Unreleased") == ""
    # The older section is untouched.
    assert "The first release." in extract_notes(updated, "1.0.0")


def test_releasing_rewrites_the_link_references():
    updated = release_unreleased(CHANGELOG, parse_version("1.1.0"), date(2026, 9, 1), REPO)
    assert f"[Unreleased]: https://github.com/{REPO}/compare/v1.1.0...HEAD" in updated
    assert f"[1.1.0]: https://github.com/{REPO}/compare/v1.0.0...v1.1.0" in updated


def test_releasing_an_empty_unreleased_section_is_refused():
    empty = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-08-25\n\n- First.\n"
    with pytest.raises(ChangelogError):
        release_unreleased(empty, parse_version("1.1.0"), date(2026, 9, 1), REPO)


def test_releasing_without_an_unreleased_section_is_refused():
    with pytest.raises(ChangelogError):
        release_unreleased("# Changelog\n", parse_version("1.1.0"), date(2026, 9, 1), REPO)
