"""Package-safe access to bundled Hipson runtime assets."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from hipson.paths import package_root


def packaged_asset(path: str) -> Path:
    return Path(str(resources.files(__name__).joinpath(path)))


def runtime_asset(path: str) -> Path:
    """Return a source-checkout asset when present, otherwise packaged data."""
    source = package_root() / path
    if source.exists():
        return source
    return packaged_asset(path)


def asset_text(path: str) -> str:
    return runtime_asset(path).read_text(encoding="utf-8")
