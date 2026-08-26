"""Single source of truth for the loader's version.

`scripts/release.ps1` rewrites `__version__` here, and the publish workflow refuses to
build a `v*` tag whose name disagrees with it, so the number in the image, the git tag,
the GHCR tag, and the CHANGELOG entry can never drift apart.
"""

from __future__ import annotations

import os

__version__ = "1.1.0"


def build_revision() -> str:
    """The git commit the image was built from.

    Baked in by the Dockerfile at build time. Local builds have no revision, which is
    itself useful information: it means the image did not come from CI.
    """
    return os.environ.get("LOADER_REVISION", "unknown")
