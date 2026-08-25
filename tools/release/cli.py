"""Release CLI.

    python -m tools.release.cli current
    python -m tools.release.cli next --bump minor
    python -m tools.release.cli set 1.2.0
    python -m tools.release.cli check --tag refs/tags/v1.2.0
    python -m tools.release.cli notes --version 1.2.0

`check` is what the publish workflow runs before it builds anything: it refuses a tag
that disagrees with app/version.py or that has no changelog entry.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timezone, datetime
from pathlib import Path

from .changelog import ChangelogError, notes_for_tag, release_unreleased
from .version import VersionError, parse_tag, parse_version, read_version, write_version

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO_ROOT / "loader" / "app" / "version.py"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
REPO_SLUG = "cbattlegear/WidestWarehouse"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.release.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("current", help="Print the version in loader/app/version.py.")

    next_cmd = sub.add_parser("next", help="Print the next version without writing anything.")
    next_cmd.add_argument("--bump", choices=("major", "minor", "patch"), required=True)

    set_cmd = sub.add_parser("set", help="Write a version and date the Unreleased changelog section.")
    set_cmd.add_argument("version")
    set_cmd.add_argument(
        "--no-changelog",
        action="store_true",
        help="Only rewrite version.py. Used when the changelog was already released.",
    )

    check_cmd = sub.add_parser("check", help="Verify a tag matches version.py and the changelog.")
    check_cmd.add_argument("--tag", required=True)

    notes_cmd = sub.add_parser("notes", help="Print one version's changelog body.")
    notes_cmd.add_argument("--version", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        if args.command == "current":
            print(read_version(VERSION_FILE))
            return 0

        if args.command == "next":
            print(read_version(VERSION_FILE).bump(args.bump))
            return 0

        if args.command == "set":
            version = parse_version(args.version)
            current = read_version(VERSION_FILE)
            if not args.no_changelog:
                today = datetime.now(timezone.utc).date()
                CHANGELOG_FILE.write_text(
                    release_unreleased(
                        CHANGELOG_FILE.read_text(encoding="utf-8"), version, today, REPO_SLUG
                    ),
                    encoding="utf-8",
                    newline="",
                )
            write_version(VERSION_FILE, version)
            print(f"{current} -> {version}")
            return 0

        if args.command == "check":
            tag_version = parse_tag(args.tag)
            file_version = read_version(VERSION_FILE)
            if str(tag_version) != str(file_version):
                print(
                    f"::error::Tag {args.tag} does not match loader/app/version.py "
                    f"({file_version}). Run scripts/release.ps1 instead of tagging by hand.",
                    file=sys.stderr,
                )
                return 1
            notes = notes_for_tag(CHANGELOG_FILE, tag_version)
            if not notes:
                print(f"::error::CHANGELOG.md has no notes for {tag_version}.", file=sys.stderr)
                return 1
            print(f"{tag_version} matches version.py and has changelog notes.")
            return 0

        if args.command == "notes":
            print(notes_for_tag(CHANGELOG_FILE, parse_version(args.version)))
            return 0

    except (VersionError, ChangelogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
