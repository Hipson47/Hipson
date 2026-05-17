"""Package-safe access to bundled Hipson runtime assets."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from hipson.paths import package_root


def packaged_asset(path: str) -> Path:
    return Path(str(resources.files(__name__).joinpath(path)))


def runtime_asset(path: str) -> Path:
    """Return a canonical source asset when present, otherwise packaged data."""
    root = package_root()
    source_asset = root / "src" / "hipson" / "assets" / path
    if source_asset.exists():
        return source_asset

    source = root / path
    if source.exists() and "codex-workflow-kit" not in Path(path).parts:
        return source
    return packaged_asset(path)


def asset_text(path: str) -> str:
    return runtime_asset(path).read_text(encoding="utf-8")
