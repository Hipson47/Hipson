"""Deterministic path and command sandbox checks for Hipson runtime tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hipson.paths import package_root
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


def check_skill_root_path(path: str | Path, cwd: Path) -> SandboxDecision:
    raw = Path(path).expanduser()
    workspace = cwd.resolve()
    if _has_parent_reference(raw):
        return SandboxDecision(False, None, "Path traversal is not allowed")
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = candidate.resolve()
    base_decision = _check_basic_path_safety(raw, resolved)
    if base_decision is not None:
        return base_decision
    if _is_relative_to(resolved, workspace) or _is_relative_to(resolved, package_root().resolve()):
        return SandboxDecision(True, resolved, "allowed")
    return SandboxDecision(False, resolved, "Skill root must stay inside the active workspace or packaged Hipson assets")


def check_skill_file_path(path: str | Path, root: str | Path | None, cwd: Path) -> SandboxDecision:
    root_decision = check_skill_root_path(root or package_root(), cwd)
    if not root_decision.allowed or root_decision.path is None:
        return root_decision
    raw = Path(path).expanduser()
    if _has_parent_reference(raw):
        return SandboxDecision(False, None, "Path traversal is not allowed")
    candidate = raw if raw.is_absolute() else root_decision.path / raw
    resolved = candidate.resolve()
    base_decision = _check_basic_path_safety(raw, resolved)
    if base_decision is not None:
        return base_decision
    if not _is_relative_to(resolved, root_decision.path):
        return SandboxDecision(False, resolved, "Skill path must stay inside the selected root")
    return SandboxDecision(True, resolved, "allowed")


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
    base_decision = _check_basic_path_safety(raw, resolved)
    if base_decision is not None:
        return base_decision
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return SandboxDecision(False, resolved, "Path must stay inside the active workspace")
    if require_generated and (not relative.parts or relative.parts[0] not in ALLOWED_GENERATED_DIRS):
        return SandboxDecision(False, resolved, "Write path must be under runs/, scans/, docs/, or memory/")
    return SandboxDecision(True, resolved, "allowed")


def _check_basic_path_safety(raw: Path, resolved: Path) -> SandboxDecision | None:
    if _has_parent_reference(raw):
        return SandboxDecision(False, None, "Path traversal is not allowed")
    if _is_broad_home_path(resolved):
        return SandboxDecision(False, resolved, "Broad home/profile paths are not allowed")
    if is_sensitive_path(resolved):
        return SandboxDecision(False, resolved, "Sensitive paths are not allowed")
    return None


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _has_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _is_broad_home_path(path: Path) -> bool:
    home = Path.home().resolve()
    if path == home:
        return True
    parts = path.parts
    return len(parts) <= 5 and len(parts) >= 4 and parts[1:3] == ("mnt", "c") and parts[3].casefold() == "users"
