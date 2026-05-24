"""Deterministic path and command sandbox checks for Hipson runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hipson.redaction import is_sensitive_path

ALLOWED_GENERATED_DIRS = frozenset({"runs", "scans", "docs", "memory"})
READ_ONLY_GIT_SUBCOMMANDS = frozenset({"branch", "diff", "log", "show", "status"})
READ_ONLY_COMMANDS = frozenset({"ls", "pwd"})


@dataclass(frozen=True)
class SandboxDecision:
    allowed: bool
    path: Path | None
    reason: str


def check_read_path(path: str | Path, cwd: Path) -> SandboxDecision:
    return _check_workspace_path(path, cwd, require_generated=False)


def check_write_path(path: str | Path, cwd: Path) -> SandboxDecision:
    return _check_workspace_path(path, cwd, require_generated=True)


def is_allowlisted_read_only_command(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if executable in READ_ONLY_COMMANDS:
        return True
    return executable == "git" and len(command) > 1 and command[1] in READ_ONLY_GIT_SUBCOMMANDS


def _check_workspace_path(path: str | Path, cwd: Path, *, require_generated: bool) -> SandboxDecision:
    raw = Path(path).expanduser()
    workspace = cwd.resolve()
    if _has_parent_reference(raw):
        return SandboxDecision(False, None, "Path traversal is not allowed")
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = candidate.resolve()
    if _is_broad_home_path(resolved):
        return SandboxDecision(False, resolved, "Broad home/profile paths are not allowed")
    if is_sensitive_path(resolved):
        return SandboxDecision(False, resolved, "Sensitive paths are not allowed")
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return SandboxDecision(False, resolved, "Path must stay inside the active workspace")
    if require_generated and (not relative.parts or relative.parts[0] not in ALLOWED_GENERATED_DIRS):
        return SandboxDecision(False, resolved, "Write path must be under runs/, scans/, docs/, or memory/")
    return SandboxDecision(True, resolved, "allowed")


def _has_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _is_broad_home_path(path: Path) -> bool:
    home = Path.home().resolve()
    if path == home:
        return True
    parts = path.parts
    return len(parts) <= 5 and len(parts) >= 4 and parts[1:3] == ("mnt", "c") and parts[3].casefold() == "users"
