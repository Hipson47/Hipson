"""Deterministic path and command sandbox checks for Hipson runtime tools."""

from __future__ import annotations

import os
import subprocess
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
    """Check argument syntax only; Git also requires prepare_read_only_command at execution."""
    if not command:
        return False
    executable = command[0]
    if executable in READ_ONLY_COMMANDS:
        return True
    # Global git options (notably -c / --config-env) are deliberately outside this grammar.
    if executable != "git" or len(command) < 2 or command[1] not in READ_ONLY_GIT_SUBCOMMANDS:
        return False
    subcommand, arguments = command[1], command[2:]
    if subcommand == "branch":
        return _is_branch_listing(arguments)
    for argument in arguments:
        if argument == "--":
            break  # Everything after the path separator is a path, not an option.
        option = argument.partition("=")[0]
        if argument.startswith("-o") or (
            option.startswith("--") and any(
                unsafe.startswith(option) for unsafe in ("--output", "--ext-diff", "--textconv")
            )
        ):
            return False
    return True


def git_filter_overrides(cwd: Path | str) -> list[str]:
    """Disable every configured clean/smudge/process filter driver for a read in cwd.

    status/diff re-hash files with stale stat data through the repository's filter drivers, which are
    arbitrary commands. Git's built-in text/EOL conversion is unaffected. Returns [] outside a repository.
    """
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    try:
        listed = subprocess.run(["git", "-c", "core.fsmonitor=false", "config", "--null", "--name-only", "--list"],
                                cwd=str(cwd), env=environment, capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    overrides: list[str] = []
    for key in sorted(set(listed.stdout.split(b"\0")[:-1])) if listed.returncode == 0 else []:
        if key.startswith(b"filter.") and key.rsplit(b".", 1)[-1] in (b"clean", b"smudge", b"process", b"required"):
            overrides.extend(("-c", os.fsdecode(key) + ("=false" if key.endswith(b".required") else "=")))
    return overrides


def prepare_read_only_command(command: list[str], *, cwd: Path | str | None = None) -> tuple[list[str], dict[str, str]]:
    """Build argv/environment together; executors must use both without shell=True.

    This is an internal execution contract, not a tool-input assertion. Approval
    callers may opt into hardened_exec only when their executor uses this pair.
    With cwd, configured Git filter drivers are disabled for that repository too.
    """
    if not is_allowlisted_read_only_command(command):
        raise ValueError("Command is not allowlisted for read-only execution")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("GIT_") and key.upper() != "PAGER"
    }
    if command[0] != "git":
        return list(command), environment
    # Keep the user's global/system config: it carries core.autocrlf (Git for Windows), safe.directory
    # and excludes, and dropping it changes what status/diff report. Helpers are disabled explicitly
    # below instead; repository-local config is loaded either way.
    environment.update(GIT_PAGER="cat", PAGER="cat", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    subcommand = command[1]
    options = ["--no-ext-diff", "--no-textconv"] if subcommand in ("diff", "log", "show") else []
    helpers = git_filter_overrides(cwd) if cwd is not None else []
    hardened = ["git", "--no-pager", "-c", "core.pager=cat", "-c", "core.fsmonitor=false",
                "-c", "diff.external=", *helpers, subcommand, *options, *command[2:]]
    return hardened, environment


def _is_branch_listing(arguments: list[str]) -> bool:
    # Branch accepts bare names as creation requests, even without an explicit write flag.
    listing = False
    takes_value = False
    for argument in arguments:
        if takes_value:
            if argument.startswith("-"):
                return False
            takes_value = False
            continue
        option, equals, _ = argument.partition("=")
        if option == "--list":
            if equals:
                return False
            listing = True
        elif argument.startswith("-") and set(argument[1:]) <= set("arv"):
            if argument == "-":
                return False
        elif option in ("--show-current", "--all", "--remotes", "--verbose", "--no-color", "--no-column", "--ignore-case"):
            if equals:
                return False
        elif option in ("--format", "--sort", "--points-at"):
            takes_value = not equals
        elif option in ("--contains", "--no-contains", "--merged", "--no-merged", "--color", "--column"):
            # Optional values must use '='; this keeps a bare branch name unambiguous.
            pass
        elif argument.startswith("-") or not listing:
            return False
    return not takes_value


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
