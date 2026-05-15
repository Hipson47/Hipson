"""Home/config directory detection for Hipson and Codex."""

from __future__ import annotations

import os
from pathlib import Path


def detect_hipson_home(env: dict[str, str] | None = None) -> tuple[Path, list[str]]:
    env = env or os.environ
    warnings: list[str] = []
    if env.get("HIPSON_HOME"):
        return Path(env["HIPSON_HOME"]).expanduser().resolve(), warnings
    if env.get("XDG_CONFIG_HOME"):
        return (Path(env["XDG_CONFIG_HOME"]).expanduser() / "hipson").resolve(), warnings
    if env.get("CODEX_USER_HOME"):
        warnings.append("CODEX_USER_HOME is legacy and ignored for Hipson config; use HIPSON_HOME.")
    return (Path.home() / ".config" / "hipson").resolve(), warnings


def detect_codex_home(env: dict[str, str] | None = None) -> tuple[Path, list[str]]:
    env = env or os.environ
    warnings: list[str] = []
    if env.get("CODEX_HOME"):
        return Path(env["CODEX_HOME"]).expanduser().resolve(), warnings
    if env.get("CODEX_USER_HOME"):
        warnings.append("CODEX_USER_HOME is deprecated for Codex; use CODEX_HOME instead.")
        legacy = Path(env["CODEX_USER_HOME"]).expanduser()
        if legacy.name == ".codex":
            return legacy.resolve(), warnings
        return (legacy / ".codex").resolve(), warnings
    return (Path.home() / ".codex").resolve(), warnings

