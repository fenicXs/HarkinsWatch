"""The worker image tag and the playwright pin must name the same version."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_playwright_version_matches_pyproject() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    pinned = re.search(r'"playwright==([\d.]+)"', pyproject)
    arg = re.search(r"^ARG PLAYWRIGHT_VERSION=([\d.]+)$", dockerfile, re.MULTILINE)
    assert pinned is not None, "pyproject.toml has no exact playwright pin"
    assert arg is not None, "Dockerfile has no PLAYWRIGHT_VERSION arg"
    assert pinned.group(1) == arg.group(1)
