#!/usr/bin/env python3
"""Hipson project orchestration helper.

Dependency-free CLI for delta scans, project memory bootstrap, and subagent
packet generation across local repositories.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from hipson.assets import runtime_asset
from hipson.home import detect_codex_home
from hipson.packets import compile_executor_packet, compile_review_packet, csv_items
from hipson.paths import package_root
from hipson.redaction import (
    is_sensitive_path,
    redact_sensitive_paths,
    redact_text,
    sanitize_path,
    summarize_sensitive_path,
)

DEFAULT_TIMEOUT = 20
MAX_EMBEDDED_DIFF_CHARS = 60000
MAX_UNTRACKED_FILE_CHARS = 12000
PACKAGE_ROOT = package_root()


@dataclass
class CommandResult:
    command: list[str]
    cwd: Path
    code: int
    stdout: str
    stderr: str


def run(command: list[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(command, cwd, proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except FileNotFoundError as exc:
        return CommandResult(command, cwd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.strip() if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        return CommandResult(command, cwd, 124, stdout, stderr or f"Timed out after {timeout}s")


def resolve_project(path: str) -> Path:
    project = Path(path).expanduser().resolve()
    if not project.exists():
        raise SystemExit(f"Project path does not exist: {project}")
    if not project.is_dir():
        raise SystemExit(f"Project path is not a directory: {project}")
    return project


def resolve_project_from_registry(path: str, registry: Path) -> Path:
    project_path = Path(path).expanduser()
    if not project_path.is_absolute():
        project_path = registry.parent / project_path
    return resolve_project(str(project_path))


def git_root(project: Path) -> Path | None:
    result = run(["git", "rev-parse", "--show-toplevel"], project)
    if result.code != 0 or not result.stdout:
        return None
    root = Path(result.stdout).resolve()
    return useful_git_root(project, root)


def useful_git_root(project: Path, root: Path) -> Path | None:
    """Ignore accidental parent git roots such as the whole user profile."""
    project = project.resolve()
    home = Path.home().resolve()
    if root == home and project != root:
        return None
    if project != root and looks_like_windows_profile(root):
        return None
    return root


def looks_like_windows_profile(path: Path) -> bool:
    return (path / "NTUSER.DAT").exists() or (path / "AppData").exists()


def detect_codex_user_home() -> Path:
    return detect_codex_home()[0]


def git_scope_args(project: Path, root: Path) -> list[str]:
    scoped = rel(project, root)
    if scoped == ".":
        return []
    return ["--", scoped]


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def discover_package_commands(project: Path) -> list[str]:
    package_json = project / "package.json"
    if not package_json.exists():
        return []

    data = read_json(package_json)
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return []

    manager = "npm"
    if (project / "pnpm-lock.yaml").exists():
        manager = "pnpm"
    elif (project / "yarn.lock").exists():
        manager = "yarn"

    commands = []
    for name in ("dev", "test", "lint", "typecheck", "build"):
        if name in scripts:
            if manager == "yarn":
                commands.append(f"yarn {name}")
            else:
                commands.append(f"{manager} run {name}")
    return commands


def discover_python_commands(project: Path) -> list[str]:
    commands = []
    requirements = ""
    if (project / "requirements.txt").exists():
        requirements = (project / "requirements.txt").read_text(encoding="utf-8", errors="replace")
    pyproject = ""
    if (project / "pyproject.toml").exists():
        pyproject = (project / "pyproject.toml").read_text(encoding="utf-8", errors="replace")

    if (project / "pytest.ini").exists() or "pytest" in requirements or "pytest" in pyproject:
        commands.append("pytest")
    if pyproject:
        if "ruff" in pyproject:
            commands.append("ruff check .")
        if "mypy" in pyproject:
            commands.append("mypy .")
    if (project / "scripts" / "run_tests.py").exists():
        commands.append("python3 scripts/run_tests.py")
    return commands


def discover_make_commands(project: Path) -> list[str]:
    makefile = project / "Makefile"
    if not makefile.exists():
        return []

    commands = []
    text = makefile.read_text(encoding="utf-8", errors="replace")
    targets = set(re.findall(r"^([a-zA-Z0-9_.-]+):(?:\s|$)", text, flags=re.MULTILINE))
    for name in ("test", "lint", "typecheck", "build"):
        if name in targets:
            commands.append(f"make {name}")
    return commands


def discover_commands(project: Path) -> list[str]:
    seen: set[str] = set()
    commands: list[str] = []
    for command in (
        discover_package_commands(project)
        + discover_python_commands(project)
        + discover_make_commands(project)
    ):
        if command not in seen:
            commands.append(command)
            seen.add(command)
    return commands


def parse_repos_yaml(path: Path) -> list[dict[str, object]]:
    """Parse the small repos.yaml subset used by this hub.

    This intentionally avoids a PyYAML dependency. Supported scalar fields are
    `name`, `path`, `type`, `progress`, and `verification`. Supported list
    fields are `risk_paths`, `owners`, `tags`, and `verification`.
    """
    repos: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    current_list_key = ""
    scalar_keys = {"name", "path", "type", "progress", "verification"}
    list_keys = {"risk_paths", "owners", "tags", "verification"}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        item_match = re.match(r"\s*-\s+name:\s*(.+?)\s*$", line)
        if item_match:
            if current:
                repos.append(current)
            current = {"name": strip_yaml_scalar(item_match.group(1))}
            current_list_key = ""
            continue

        list_item_match = re.match(r"\s{6}-\s+(.+?)\s*$", line)
        if list_item_match and current is not None and current_list_key:
            values = current.setdefault(current_list_key, [])
            if isinstance(values, list):
                values.append(strip_yaml_scalar(list_item_match.group(1)))
            continue

        field_match = re.match(r"\s{4}([a-zA-Z_][a-zA-Z0-9_-]*):(?:\s*(.*?)\s*)?$", line)
        if field_match and current is not None:
            key, value = field_match.groups()
            current_list_key = ""
            if key in list_keys and not value:
                current[key] = []
                current_list_key = key
                continue
            if key in scalar_keys and value:
                current[key] = strip_yaml_scalar(value)

    if current:
        repos.append(current)
    return repos


def strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def changed_files(project: Path, root: Path | None) -> list[str]:
    if not root:
        return []

    files = []
    for command in (
        ["git", "diff", "--name-only", *git_scope_args(project, root)],
        ["git", "diff", "--cached", "--name-only", *git_scope_args(project, root)],
    ):
        result = run(command, root)
        if result.code == 0:
            for line in result.stdout.splitlines():
                file_name = sanitize_path(line.strip())
                if file_name and file_name not in files:
                    files.append(file_name)

    untracked = run(["git", "ls-files", "--others", "--exclude-standard", *git_scope_args(project, root)], root)
    if untracked.code != 0:
        return files
    for line in untracked.stdout.splitlines():
        file_name = sanitize_path(line.strip())
        if file_name and file_name not in files:
            files.append(file_name)
    return files


def untracked_files(project: Path, root: Path | None) -> list[str]:
    if not root:
        return []
    result = run(["git", "ls-files", "--others", "--exclude-standard", *git_scope_args(project, root)], root)
    if result.code != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def should_embed_file(path: str) -> bool:
    if is_sensitive_path(path):
        return False
    parts = Path(path).parts
    blocked_parts = {".git", ".next", "node_modules", "dist", "build", "coverage"}
    if any(part in blocked_parts for part in parts):
        return False
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        return False
    blocked_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".zip", ".lock"}
    if Path(path).suffix.lower() in blocked_suffixes:
        return False
    if Path(path).name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}:
        return False
    return True


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} chars]\n"


def embedded_untracked_content(project: Path, root: Path | None) -> str:
    if not root:
        return ""

    chunks = []
    for file_name in untracked_files(project, root):
        if is_sensitive_path(file_name):
            chunks.extend(["### Untracked file: [sensitive]", summarize_sensitive_path(file_name), ""])
            continue
        if not should_embed_file(file_name):
            continue
        path = root / file_name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.extend(
            [
                f"### Untracked file: `{file_name}`",
                "```text",
                redact_text(truncate(text, MAX_UNTRACKED_FILE_CHARS)),
                "```",
                "",
            ]
        )
    return "\n".join(chunks).strip()


def maybe_file(project: Path, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        path = project / candidate
        if path.exists():
            return candidate
    return None


def markdown_list(items: Iterable[str], empty: str = "none") -> str:
    values = [item for item in items if item]
    if not values:
        return f"- {empty}"
    return "\n".join(f"- `{item}`" for item in values)


def build_scan(project: Path, include_diff: bool, diff_lines: int) -> str:
    root = git_root(project)
    scan_cwd = root or project
    scope_args = git_scope_args(project, root) if root else []

    status = run(["git", "status", "--short", "--porcelain=v1", *scope_args], scan_cwd) if root else None
    branch = run(["git", "branch", "--show-current"], scan_cwd) if root else None
    unstaged_stat = run(["git", "diff", "--stat", *scope_args], scan_cwd) if root else None
    staged_stat = run(["git", "diff", "--cached", "--stat", *scope_args], scan_cwd) if root else None
    diff_names = changed_files(project, root)
    log = run(["git", "log", "--oneline", "-n", "10", *scope_args], scan_cwd) if root else None
    commands = discover_commands(project)

    progress = maybe_file(project, ("docs/hipson-progress.md", "docs/progress.md", "CHANGELOG.md"))
    agents = maybe_file(project, ("AGENTS.md", ".agents.md"))

    lines = [
        "# Hipson Delta Scan",
        "",
        f"- Project: `{project}`",
        f"- Git root: `{root if root else 'not found'}`",
        f"- Branch: `{branch.stdout if branch and branch.stdout else 'unknown'}`",
        "",
        "## Important Files",
        f"- AGENTS: `{agents or 'not found'}`",
        f"- Progress/changelog: `{progress or 'not found'}`",
        "",
        "## Discovered Commands",
        markdown_list(commands),
        "",
        "## Git Status",
        "```text",
        redact_sensitive_paths(status.stdout) if status and status.stdout else "clean or unavailable",
        "```",
        "",
        "## Changed Files",
        markdown_list(diff_names),
        "",
        "## Unstaged Diff Stat",
        "```text",
        redact_sensitive_paths(unstaged_stat.stdout) if unstaged_stat and unstaged_stat.stdout else "none",
        "```",
        "",
        "## Staged Diff Stat",
        "```text",
        redact_sensitive_paths(staged_stat.stdout) if staged_stat and staged_stat.stdout else "none",
        "```",
        "",
        "## Recent Commits",
        "```text",
        log.stdout if log and log.stdout else "unavailable",
        "```",
    ]

    if include_diff and root:
        unstaged_diff = run(["git", "diff", f"--unified={diff_lines}", *scope_args], scan_cwd, timeout=60)
        staged_diff = run(["git", "diff", "--cached", f"--unified={diff_lines}", *scope_args], scan_cwd, timeout=60)
        untracked = embedded_untracked_content(project, root)
        unstaged_body = truncate(redact_sensitive_paths(unstaged_diff.stdout) if unstaged_diff.stdout else "none", MAX_EMBEDDED_DIFF_CHARS)
        staged_body = truncate(redact_sensitive_paths(staged_diff.stdout) if staged_diff.stdout else "none", MAX_EMBEDDED_DIFF_CHARS)
        lines.extend(["", "## Untracked File Contents", untracked or "none"])
        lines.extend(["", "## Unstaged Diff", "```diff", unstaged_body, "```"])
        lines.extend(["", "## Staged Diff", "```diff", staged_body, "```"])

    return redact_text("\n".join(lines).rstrip() + "\n")


def ensure_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def build_scan_record(repo: dict[str, object], include_diff: bool = False) -> dict[str, object]:
    project = resolve_project(str(repo["path"]))
    root = git_root(project)
    scope_args = git_scope_args(project, root) if root else []
    status = run(["git", "status", "--short", "--porcelain=v1", *scope_args], root) if root else None
    branch = run(["git", "branch", "--show-current"], root) if root else None
    unstaged_stat = run(["git", "diff", "--stat", *scope_args], root) if root else None
    staged_stat = run(["git", "diff", "--cached", "--stat", *scope_args], root) if root else None
    log = run(["git", "log", "--oneline", "-n", "5", *scope_args], root) if root else None
    unstaged_diff = run(["git", "diff", "--unified=3", *scope_args], root, timeout=60) if include_diff and root else None
    staged_diff = run(["git", "diff", "--cached", "--unified=3", *scope_args], root, timeout=60) if include_diff and root else None

    return {
        "name": repo.get("name", project.name),
        "path": str(project),
        "type": repo.get("type", ""),
        "git_root": str(root) if root else None,
        "branch": branch.stdout if branch and branch.stdout else None,
        "progress": repo.get("progress") or maybe_file(project, ("docs/hipson-progress.md", "docs/progress.md", "CHANGELOG.md")),
        "risk_paths": ensure_string_list(repo.get("risk_paths")),
        "owners": ensure_string_list(repo.get("owners")),
        "tags": ensure_string_list(repo.get("tags")),
        "verification": ensure_string_list(repo.get("verification")),
        "commands": discover_commands(project),
        "status": redact_text(redact_sensitive_paths(status.stdout if status else "")),
        "changed_files": changed_files(project, root),
        "untracked_files": untracked_files(project, root),
        "unstaged_diff_stat": redact_text(redact_sensitive_paths(unstaged_stat.stdout if unstaged_stat else "")),
        "staged_diff_stat": redact_text(redact_sensitive_paths(staged_stat.stdout if staged_stat else "")),
        "recent_commits": redact_text(log.stdout if log else ""),
        "unstaged_diff": redact_text(redact_sensitive_paths(unstaged_diff.stdout if unstaged_diff else "")),
        "staged_diff": redact_text(redact_sensitive_paths(staged_diff.stdout if staged_diff else "")),
        "untracked_content": embedded_untracked_content(project, root) if include_diff and root else "",
    }


def render_multi_scan(records: list[dict[str, object]]) -> str:
    lines = ["# Hipson Multi-Repo Scan", ""]
    for record in records:
        lines.extend(
            [
                f"## {record['name']}",
                f"- Path: `{record['path']}`",
                f"- Type: `{record.get('type') or 'unknown'}`",
                f"- Branch: `{record.get('branch') or 'unknown'}`",
                f"- Progress: `{record.get('progress') or 'not found'}`",
                f"- Owners: `{', '.join(record.get('owners', [])) or 'none'}`",
                f"- Tags: `{', '.join(record.get('tags', [])) or 'none'}`",
                "",
                "### Risk Paths",
                markdown_list(record.get("risk_paths", [])),  # type: ignore[arg-type]
                "",
                "### Registry Verification",
                markdown_list(record.get("verification", [])),  # type: ignore[arg-type]
                "",
                "### Commands",
                markdown_list(record.get("commands", [])),  # type: ignore[arg-type]
                "",
                "### Status",
                "```text",
                str(record.get("status") or "clean or unavailable"),
                "```",
                "",
                "### Changed Files",
                markdown_list(record.get("changed_files", [])),  # type: ignore[arg-type]
                "",
                "### Untracked Files",
                markdown_list(record.get("untracked_files", [])),  # type: ignore[arg-type]
                "",
                "### Unstaged Diff Stat",
                "```text",
                str(record.get("unstaged_diff_stat") or "none"),
                "```",
                "",
                "### Staged Diff Stat",
                "```text",
                str(record.get("staged_diff_stat") or "none"),
                "```",
                "",
                "### Recent Commits",
                "```text",
                str(record.get("recent_commits") or "unavailable"),
                "```",
                "",
            ]
        )
        if record.get("untracked_content"):
            lines.extend(["### Untracked File Contents", str(record.get("untracked_content")), ""])
        if record.get("unstaged_diff"):
            lines.extend(["### Unstaged Diff", "```diff", str(record.get("unstaged_diff")), "```", ""])
        if record.get("staged_diff"):
            lines.extend(["### Staged Diff", "```diff", str(record.get("staged_diff")), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_output(text: str, output: str | None) -> None:
    text = redact_text(text)
    if output:
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")
    else:
        print(text)


def command_scan(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    text = build_scan(project, include_diff=args.include_diff, diff_lines=args.diff_lines)
    write_output(text, args.output)


def command_init(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    target = project / "docs" / "hipson-progress.md"
    if target.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing file: {target}. Use --force to replace it.")

    target.parent.mkdir(parents=True, exist_ok=True)
    template = runtime_asset("templates/hipson-progress.md")
    text = template.read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"Created {target}")


def command_scan_many(args: argparse.Namespace) -> None:
    registry = Path(args.registry).expanduser().resolve()
    if not registry.exists():
        raise SystemExit(f"Registry file does not exist: {registry}")

    repos = parse_repos_yaml(registry)
    if not repos:
        raise SystemExit(f"No repos found in registry: {registry}")

    records = []
    for repo in repos:
        repo = dict(repo)
        repo["path"] = str(resolve_project_from_registry(str(repo["path"]), registry))
        records.append(build_scan_record(repo, include_diff=args.include_diff))
    markdown = render_multi_scan(records)
    write_output(markdown, args.output)

    if args.json_output:
        json_path = Path(args.json_output).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {json_path}")


def command_check_setup(args: argparse.Namespace) -> None:
    home = detect_codex_user_home()
    required_checks = {
        "repo_registry": Path(args.registry).expanduser().resolve(),
        "orchestrator_doc": PACKAGE_ROOT / "ORCHESTRATOR.md",
    }
    optional_checks = {
        "global_agents": home / "AGENTS.md",
        "hipson_skill": home / "skills" / "hipson-workflow" / "SKILL.md",
    }

    failed = False
    for name, path in required_checks.items():
        ok = path.exists()
        print(f"{name}: {'ok' if ok else 'missing'} - {path}")
        if name == "repo_registry" and not ok:
            print("hint: copy repos.example.yaml to repos.yaml and adjust local paths")
        failed = failed or not ok

    for name, path in optional_checks.items():
        ok = path.exists()
        label = "ok" if ok else "missing optional"
        print(f"{name}: {label} - {path}")
        failed = failed or (args.require_global and not ok)

    tools = ["git", "python3"]
    for tool in tools:
        result = run(["bash", "-lc", f"command -v {tool}"], Path.cwd())
        ok = result.code == 0 and bool(result.stdout)
        print(f"tool_{tool}: {'ok' if ok else 'missing'}")
        failed = failed or not ok

    if failed:
        raise SystemExit(1)


def packet_context(project: Path, title: str, scope: str, include_diff: bool) -> str:
    scan = build_scan(project, include_diff=include_diff, diff_lines=3)
    files = changed_files(project, git_root(project))
    commands = discover_commands(project)
    return "\n".join(
        [
            f"## Task: {title}",
            "",
            "### Context",
            f"- Project: `{project}`",
            f"- Scope: {scope}",
            "",
            "### Delta scan",
            scan,
            "### Files from current diff",
            markdown_list(files),
            "",
            "### Discovered verification commands",
            markdown_list(commands),
        ]
    ).rstrip()


def command_review_packet(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    scan = build_scan(project, include_diff=args.include_diff, diff_lines=3)
    root = git_root(project)
    text = compile_review_packet(
        title=args.title,
        project=str(project),
        scope=args.scope,
        scan=scan,
        changed_files=changed_files(project, root),
        commands=discover_commands(project),
        selected_skills=csv_items(args.skills),
    )
    write_output(text, args.output)


def command_executor_packet(args: argparse.Namespace) -> None:
    project = resolve_project(args.project)
    scan = build_scan(project, include_diff=False, diff_lines=3)
    root = git_root(project)
    allowed = csv_items(args.allowed_edit) or ["[fill in allowed files/directories]"]
    inspect = csv_items(args.inspect) or ["[fill in files to inspect]"]
    verification = args.verification or "[fill in exact command after repo scan]"
    text = compile_executor_packet(
        title=args.title,
        goal=args.goal,
        project=str(project),
        scope=args.scope,
        scan=scan,
        changed_files=changed_files(project, root),
        commands=discover_commands(project),
        files_to_inspect=inspect,
        allowed_edit=allowed,
        acceptance=args.acceptance,
        verification=verification,
        selected_skills=csv_items(args.skills),
    )
    write_output(text, args.output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hipson project orchestration helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Print a repo delta scan")
    scan.add_argument("project", help="Project directory")
    scan.add_argument("--include-diff", action="store_true", help="Include git diff body")
    scan.add_argument("--diff-lines", type=int, default=3, help="Unified diff context lines")
    scan.add_argument("-o", "--output", help="Write markdown to a file")
    scan.set_defaults(func=command_scan)

    scan_many = subparsers.add_parser("scan-many", help="Scan repos from repos.yaml")
    scan_many.add_argument("registry", help="Repo registry YAML")
    scan_many.add_argument("--include-diff", action="store_true", help="Include diff bodies in JSON records")
    scan_many.add_argument("-o", "--output", help="Write markdown to a file")
    scan_many.add_argument("--json", dest="json_output", help="Write JSON scan records to a file")
    scan_many.set_defaults(func=command_scan_many)

    init = subparsers.add_parser("init", help="Create docs/hipson-progress.md in a project")
    init.add_argument("project", help="Project directory")
    init.add_argument("--force", action="store_true", help="Overwrite existing progress file")
    init.set_defaults(func=command_init)

    check = subparsers.add_parser("check-setup", help="Check local Hipson orchestrator setup")
    check.add_argument("--registry", default="repos.yaml", help="Repo registry YAML")
    check.add_argument("--require-global", action="store_true", help="Fail if global Codex kit is not installed")
    check.set_defaults(func=command_check_setup)

    review = subparsers.add_parser("review-packet", help="Generate a read-only review subagent packet")
    review.add_argument("project", help="Project directory")
    review.add_argument("--title", required=True, help="Review title")
    review.add_argument("--scope", default="current git delta", help="Review scope")
    review.add_argument("--include-diff", action="store_true", help="Embed diff body")
    review.add_argument("--skills", help="Comma-separated skills or references to include in the packet contract")
    review.add_argument("-o", "--output", help="Write markdown to a file")
    review.set_defaults(func=command_review_packet)

    executor = subparsers.add_parser("executor-packet", help="Generate an implementation subagent packet")
    executor.add_argument("project", help="Project directory")
    executor.add_argument("--title", required=True, help="Task title")
    executor.add_argument("--goal", required=True, help="Concrete implementation goal")
    executor.add_argument("--scope", default="next bounded task", help="Task scope")
    executor.add_argument("--inspect", help="Comma-separated files to inspect")
    executor.add_argument("--allowed-edit", help="Comma-separated files or directories allowed for edits")
    executor.add_argument("--acceptance", default="[fill in observable success condition]", help="Acceptance criterion")
    executor.add_argument("--verification", help="Exact verification command")
    executor.add_argument("--skills", help="Comma-separated skills or references to include in the packet contract")
    executor.add_argument("-o", "--output", help="Write markdown to a file")
    executor.set_defaults(func=command_executor_packet)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
