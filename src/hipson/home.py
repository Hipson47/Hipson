"""Home/config directory detection for Hipson and Codex."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def detect_hipson_home(env: Mapping[str, str] | None = None) -> tuple[Path, list[str]]:
    effective_env = env if env is not None else os.environ
    warnings: list[str] = []
    if effective_env.get("HIPSON_HOME"):
        return Path(effective_env["HIPSON_HOME"]).expanduser().resolve(), warnings
    if effective_env.get("XDG_CONFIG_HOME"):
        return (Path(effective_env["XDG_CONFIG_HOME"]).expanduser() / "hipson").resolve(), warnings
    if effective_env.get("CODEX_USER_HOME"):
        warnings.append("CODEX_USER_HOME is legacy and ignored for Hipson config; use HIPSON_HOME.")
    return (Path.home() / ".config" / "hipson").resolve(), warnings


def detect_codex_home(env: Mapping[str, str] | None = None) -> tuple[Path, list[str]]:
    effective_env = env if env is not None else os.environ
    warnings: list[str] = []
    if effective_env.get("CODEX_HOME"):
        return Path(effective_env["CODEX_HOME"]).expanduser().resolve(), warnings
    if effective_env.get("CODEX_USER_HOME"):
        warnings.append("CODEX_USER_HOME is deprecated for Codex; use CODEX_HOME instead.")
        legacy = Path(effective_env["CODEX_USER_HOME"]).expanduser()
        if legacy.name == ".codex":
            return legacy.resolve(), warnings
        return (legacy / ".codex").resolve(), warnings
    return (Path.home() / ".codex").resolve(), warnings
