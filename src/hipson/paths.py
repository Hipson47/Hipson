"""Path discovery for source-tree and editable installs."""

from __future__ import annotations

import os
from pathlib import Path


def _is_hipson_root(path: Path) -> bool:
    return (
        (path / "ORCHESTRATOR.md").is_file()
        and (path / "config" / "agents.json").is_file()
        and (path / "src" / "hipson").is_dir()
    )


def _module_source_root() -> Path | None:
    for candidate in Path(__file__).resolve().parents:
        if _is_hipson_root(candidate):
            return candidate
    return None


def package_root() -> Path:
    dev_root = os.environ.get("HIPSON_DEV_ROOT")
    if dev_root:
        root = Path(dev_root).expanduser().resolve()
        if not _is_hipson_root(root):
            raise SystemExit(f"Invalid HIPSON_DEV_ROOT: {root}")
        return root

    source_root = _module_source_root()
    if source_root:
        return source_root

    return Path(__file__).resolve().parent
