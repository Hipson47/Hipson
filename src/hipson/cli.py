"""Hipson CLI."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from hipson import __version__, agents, memory
from hipson import project as project_mod
from hipson.assets import runtime_asset
from hipson.codex_install import format_install_plan, install_codex
from hipson.home import detect_codex_home, detect_hipson_home
from hipson.paths import package_root
from hipson.project import (
    build_scan,
    build_scan_record,
    discover_commands,
    parse_repos_yaml,
    render_multi_scan,
    resolve_project_from_registry,
    write_output,
)
from hipson.skills import format_validation_results, validate_skills

PACKAGE_ROOT = package_root()


def command_doctor(args: argparse.Namespace) -> int:
    codex_home, codex_warnings = detect_codex_home()
    hipson_home, hipson_warnings = detect_hipson_home()
    skill_results = validate_skills(PACKAGE_ROOT)
    skill_failures = [result for result in skill_results if not result.ok]
    git_available = shutil.which("git") is not None
    cwd_git = project_mod.git_root(Path.cwd()) if git_available else None
    config_path = runtime_asset("config/agents.json")
    provider_env_paths = agents.provider_env_paths()
    existing_provider_env = next((path for path in provider_env_paths if path.exists()), None)
    assets = {
        "codex_agents": runtime_asset("codex-workflow-kit/global/AGENTS.md").exists(),
        "hipson_skill": runtime_asset("codex-workflow-kit/skills/hipson-workflow/SKILL.md").exists(),
        "agent_config": config_path.exists(),
        "templates": runtime_asset("templates/hipson-progress.md").exists(),
    }
    checks = {
        "python_version": sys.version.split()[0],
        "package_version": __version__,
        "git": "ok" if git_available else "missing",
        "cwd": str(Path.cwd()),
        "cwd_git_root": str(cwd_git) if cwd_git else "not a git repo or unavailable",
        "codex_home": str(codex_home),
        "hipson_home": str(hipson_home),
        "sidecar_env": f"found: {existing_provider_env}" if existing_provider_env else "missing optional",
        "config_readable": config_path.exists(),
        "assets": assets,
        "skills_checked": len(skill_results),
        "skills_failed": len(skill_failures),
        "commands": discover_commands(Path.cwd()),
        "warnings": codex_warnings + hipson_warnings,
    }
    failed = (not git_available) or (not config_path.exists()) or any(not ok for ok in assets.values()) or bool(skill_failures)
    if args.json:
        print(json.dumps({**checks, "ok": not failed}, indent=2))
        return 1 if failed else 0

    print(f"python: {checks['python_version']}")
    print(f"hipson: {checks['package_version']}")
    print(f"git: {checks['git']}")
    print(f"cwd: {checks['cwd']}")
    print(f"cwd_git_root: {checks['cwd_git_root']}")
    print(f"codex_home: {checks['codex_home']}")
    print(f"hipson_home: {checks['hipson_home']}")
    print(f"sidecar_env: {checks['sidecar_env']}")
    print(f"config_readable: {'ok' if checks['config_readable'] else 'missing'}")
    print("assets:")
    for name, ok in assets.items():
        print(f"- {name}: {'ok' if ok else 'missing'}")
    print(f"skills: {len(skill_results)} checked, {len(skill_failures)} failed")
    print("commands:")
    for command in checks["commands"] or ["none discovered"]:
        print(f"- {command}")
    if checks["warnings"]:
        print("warnings:")
        for warning in checks["warnings"]:
            print(f"- {warning}")
    return 1 if failed else 0


def command_scan(args: argparse.Namespace) -> int:
    try:
        project = project_mod.resolve_project(args.path)
        write_output(build_scan(project, include_diff=args.include_diff, diff_lines=args.diff_lines), args.output)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_scan_many(args: argparse.Namespace) -> int:
    registry = Path(args.registry).expanduser().resolve()
    if not registry.exists():
        print(f"Registry file does not exist: {registry}", file=sys.stderr)
        return 1
    repos = parse_repos_yaml(registry)
    if not repos:
        print(f"No repos found in registry: {registry}", file=sys.stderr)
        return 1
    records = []
    for repo in repos:
        repo = dict(repo)
        repo["path"] = str(resolve_project_from_registry(repo["path"], registry))
        records.append(build_scan_record(repo, include_diff=args.include_diff))
    write_output(render_multi_scan(records), args.output)
    if args.json_output:
        json_path = Path(args.json_output).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {json_path}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    try:
        project_mod.command_init(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_check_setup(args: argparse.Namespace) -> int:
    try:
        project_mod.command_check_setup(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_skill_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    results = validate_skills(root)
    print(format_validation_results(results))
    return 1 if any(not result.ok for result in results) else 0


def command_install_codex(args: argparse.Namespace) -> int:
    if args.apply == args.dry_run:
        print("Choose exactly one of --dry-run or --apply.", file=sys.stderr)
        return 2
    plan = install_codex(dry_run=args.dry_run)
    print(format_install_plan(plan, dry_run=args.dry_run))
    return 0


def command_packet_review(args: argparse.Namespace) -> int:
    try:
        project_mod.command_review_packet(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_packet_exec(args: argparse.Namespace) -> int:
    try:
        project_mod.command_executor_packet(args)
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


def command_sidecar_list(args: argparse.Namespace) -> int:
    try:
        agents.command_list(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_sidecar_run(args: argparse.Namespace) -> int:
    try:
        agents.command_run(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def command_memory(args: argparse.Namespace) -> int:
    try:
        args.memory_func(args)
        return 0
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return int(exc.code or 1) if isinstance(exc.code, int) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hipson", description="Hipson Codex-native local dev workflow tool")
    parser.add_argument("--version", action="version", version=f"hipson {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local Hipson prerequisites")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable doctor output")
    doctor.set_defaults(func=command_doctor)

    scan = subparsers.add_parser("scan", help="Scan a repository")
    scan.add_argument("path")
    scan.add_argument("--include-diff", action="store_true")
    scan.add_argument("--diff-lines", type=int, default=3)
    scan.add_argument("-o", "--output", help="Write markdown to a file")
    scan.set_defaults(func=command_scan)

    scan_many = subparsers.add_parser("scan-many", help="Scan repos from a registry")
    scan_many.add_argument("registry", nargs="?", default="repos.yaml", help="Repo registry YAML")
    scan_many.add_argument("--include-diff", action="store_true", help="Include staged and unstaged diff bodies")
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

    skill = subparsers.add_parser("skill", help="Skill commands")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    validate = skill_sub.add_parser("validate", help="Validate Codex SKILL.md files")
    validate.add_argument("--root", default=str(PACKAGE_ROOT))
    validate.set_defaults(func=command_skill_validate)

    install = subparsers.add_parser("install", help="Install integrations")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    codex = install_sub.add_parser("codex", help="Install Hipson into Codex")
    mode = codex.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    codex.set_defaults(func=command_install_codex)

    packet = subparsers.add_parser("packet", help="Generate bounded agent packets")
    packet_sub = packet.add_subparsers(dest="packet_command", required=True)
    review = packet_sub.add_parser("review", help="Generate a read-only review packet")
    review.add_argument("project", help="Project directory")
    review.add_argument("--title", required=True, help="Review title")
    review.add_argument("--scope", default="current git delta", help="Review scope")
    review.add_argument("--include-diff", action="store_true", help="Embed staged and unstaged diff bodies")
    review.add_argument("--skills", help="Comma-separated skills or references to include in the packet contract")
    review.add_argument("-o", "--output", help="Write markdown to a file")
    review.set_defaults(func=command_packet_review)

    executor = packet_sub.add_parser("exec", help="Generate an implementation packet")
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
    executor.set_defaults(func=command_packet_exec)

    sidecar = subparsers.add_parser("sidecar", help="Run optional API sidecar agents")
    sidecar.add_argument("--config", default=str(agents.DEFAULT_CONFIG), help="Agent config JSON")
    sidecar.add_argument("--env", help="Provider env file")
    sidecar_sub = sidecar.add_subparsers(dest="sidecar_command", required=True)
    sidecar_list = sidecar_sub.add_parser("list", help="List configured sidecar agents")
    sidecar_list.set_defaults(func=command_sidecar_list)
    sidecar_route = sidecar_sub.add_parser("route", help="Suggest sidecar agents for a task")
    sidecar_route.add_argument("--task", required=True, help="Task description")
    sidecar_route.add_argument("--risk", default="normal", help="Risk hint, e.g. normal, high, security, architecture, ui")
    sidecar_route.add_argument("--context-chars", type=int, default=0, help="Estimated packet size")
    sidecar_route.add_argument("--sensitive", action="store_true", help="Whether the packet contains sensitive context")
    sidecar_route.add_argument("--file", action="append", help="Relevant file path for LLM routing summary; repeatable")
    sidecar_route.add_argument("--skills", help="Comma-separated skills for LLM routing summary")
    sidecar_route.add_argument("--task-type", default="review", help="Task type for LLM routing summary")
    sidecar_route.add_argument("--llm", action="store_true", help="Use optional provider-backed router on redacted summary")
    sidecar_route.add_argument("--llm-dry-run", action="store_true", help="Print LLM router summary without calling provider")
    sidecar_route.add_argument("--limit", type=int, default=3)
    sidecar_route.set_defaults(func=agents.command_route)
    sidecar_run = sidecar_sub.add_parser("run", help="Run a sidecar agent on a packet")
    sidecar_run.add_argument("--agent", required=True, help="Agent name from config")
    sidecar_run.add_argument("--packet", required=True, help="Markdown packet path")
    sidecar_run.add_argument("-o", "--output", help="Output report path")
    sidecar_run.add_argument("--dry-run", action="store_true", help="Print provider request without calling API")
    sidecar_run.add_argument("--max-packet-chars", type=int, default=agents.DEFAULT_MAX_PACKET_CHARS, help="Maximum packet characters sent to provider")
    sidecar_run.set_defaults(func=command_sidecar_run)

    memory_parser = subparsers.add_parser("memory", help="Manage local Hipson memory")
    memory_parser.add_argument("--memory-dir", help="Memory directory; defaults to repo-local memory/")
    memory_sub = memory_parser.add_subparsers(dest="memory_command", required=True)
    memory_add = memory_sub.add_parser("add", help="Add a durable memory note")
    memory_add.add_argument("--scope", default="global", help="Memory scope")
    memory_add.add_argument("--repo", default="", help="Repository path or name")
    memory_add.add_argument("--kind", default="note", help="Memory kind, e.g. decision, risk, handoff")
    memory_add.add_argument("--summary", required=True, help="Memory summary")
    memory_add.add_argument("--tags", help="Comma-separated tags")
    memory_add.add_argument("--source", action="append", help="Source path or reference; repeatable")
    memory_add.add_argument("--confidence", type=float, default=1.0)
    memory_add.set_defaults(func=command_memory, memory_func=memory.command_add)
    memory_search = memory_sub.add_parser("search", help="Search memory notes")
    memory_search.add_argument("query")
    memory_search.add_argument("--repo")
    memory_search.add_argument("--scope")
    memory_search.add_argument("--limit", type=int, default=5)
    memory_search.set_defaults(func=command_memory, memory_func=memory.command_search)
    memory_list = memory_sub.add_parser("list", help="List recent memory notes")
    memory_list.add_argument("--repo")
    memory_list.add_argument("--scope")
    memory_list.add_argument("--limit", type=int, default=20)
    memory_list.set_defaults(func=command_memory, memory_func=memory.command_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
