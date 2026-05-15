"""Path discovery for source-tree and editable installs."""

from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "ORCHESTRATOR.md").exists() and (candidate / "config" / "agents.json").exists():
            return candidate
    return Path(__file__).resolve().parent
