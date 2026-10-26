"""Plan and apply a consent-gated, local lead setup without rewriting client config."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hipson import __version__, ledger
from hipson.codex_install import backup_file
from hipson.home import detect_codex_home
from hipson.project import resolve_project
from hipson.redaction import redact_text

CLIENTS = ("auto", "claude", "codex", "none")
TIMEOUT = 15


@dataclass
class InitPlan:
    report: dict[str, Any]
    snapshots: dict[Path, bytes | None] = field(default_factory=dict)


def executable() -> str:
    """Prefer the running installation, including uv tool symlinks and Windows launchers."""
    name = "hipson.exe" if sys.platform == "win32" else "hipson"
    invoked = Path(sys.argv[0]).absolute()
    candidates = [invoked] if invoked.name in ("hipson", "hipson.exe") else []
    candidates.append(Path(sys.executable).parent / name)
    located = shutil.which(name)
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise ValueError("Cannot locate the installed hipson executable; install Hipson or activate its environment.")


def _client_executable(name: str) -> str | None:
    return shutil.which(name) or (shutil.which(f"{name}.cmd") if sys.platform == "win32" else None)


def format_command(argv: list[str]) -> str:
    # PowerShell's call operator invokes quoted executable paths, including paths with spaces.
    if sys.platform == "win32":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    return shlex.join(argv)


def _run(argv: list[str], project: Path, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    command: str | list[str] = argv
    env = None
    if sys.platform == "win32" and Path(argv[0]).suffix.lower() in (".cmd", ".bat"):
        # cmd.exe expands each environment reference once. Quoting every argument and
        # disabling delayed expansion preserves spaces, %, ! and shell metacharacters.
        if any('"' in arg or "\n" in arg or "\r" in arg for arg in argv):
            raise ValueError("Cannot pass quotes or newlines to a Windows batch client.")
        env = dict(os.environ)
        for index, arg in enumerate(argv):
            # Batch launchers conventionally forward %* to an executable. Escape
            # trailing backslashes before the closing quote for that argv parser.
            trailing = len(arg) - len(arg.rstrip("\\"))
            env[f"HIPSON_INIT_ARG_{index}"] = arg + "\\" * trailing
        arguments = " ".join(f'"%HIPSON_INIT_ARG_{index}%"' for index in range(len(argv)))
        cmd = os.environ.get("COMSPEC", "cmd.exe")
        command = f'"{cmd}" /d /v:off /s /c "{arguments}"'
    return subprocess.run(
        command, cwd=project, env=env, input=input_text, stdin=subprocess.DEVNULL if input_text is None else None,
        capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT, check=False,
    )


def _snapshot(plan: InitPlan, path: Path) -> bytes | None:
    content = path.read_bytes() if path.exists() else None
    plan.snapshots[path] = content
    return content


def _ledger_version(path: Path) -> int | None:
    if not path.exists():
        return None
    # Reuse the no-sidecar reader: even dry-run against a live WAL must not create files.
    with closing(ledger._sidecar_read_only_connection(path, time.monotonic() + TIMEOUT)) as connection:
        row = connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
    if row is None or row[0] not in (1, 2, 3, ledger.SCHEMA_VERSION):
        raise ValueError("Unsupported task ledger schema; setup has made no changes.")
    return int(row[0])


def _project_arg(args: list[str]) -> str | None:
    for index, arg in enumerate(args):
        if arg == "--project" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--project="):
            return arg.split("=", 1)[1]
    return None


def _resolved_path(value: str, project: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project / path).resolve()


def _registration_matches(existing: Any, command: str, args: list[str], project: Path) -> bool:
    if not isinstance(existing, dict) or existing.get("type", "stdio") != "stdio" or existing.get("enabled", True) is not True:
        return False
    current_command, current_args = existing.get("command"), existing.get("args", [])
    if not isinstance(current_command, str) or not isinstance(current_args, list) or not all(isinstance(arg, str) for arg in current_args):
        return False
    located = shutil.which(current_command) if Path(current_command).name == current_command else None
    if _resolved_path(located or current_command, project) != _resolved_path(command, project):
        return False

    def normalized(arguments: list[str]) -> list[str]:
        result = []
        index = 0
        while index < len(arguments):
            arg = arguments[index]
            if arg == "--project" and index + 1 < len(arguments):
                index += 1
                result.extend(["--project", os.path.normcase(str(_resolved_path(arguments[index], project)))])
            elif arg.startswith("--project="):
                result.extend(["--project", os.path.normcase(str(_resolved_path(arg.split("=", 1)[1], project)))])
            else:
                result.append(arg)
            index += 1
        return result

    return normalized(current_args) == normalized(args)


def _registration_description(scope: str, existing: Any) -> str:
    entry = existing if isinstance(existing, dict) else {}
    args = entry.get("args", [])
    project = _project_arg(args) if isinstance(args, list) and all(isinstance(arg, str) for arg in args) else None
    # Config can contain credentials in env or unrelated arguments. Only identify
    # the command and project; do not copy the entire server entry into reports.
    return redact_text(f"{scope} scope (command={entry.get('command')!r}, project={project!r})")


def _claude_registrations(plan: InitPlan, project: Path, user_config: Path) -> list[tuple[str, Any]]:
    def read(path: Path) -> dict[str, Any]:
        original = _snapshot(plan, path)
        try:
            parsed = json.loads(original) if original is not None else {}
            if not isinstance(parsed, dict):
                raise ValueError("expected a JSON object")
            return parsed
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Cannot safely inspect Claude config at {path}: invalid JSON.") from exc

    user = read(user_config)
    shared = read(project / ".mcp.json")
    projects = user.get("projects", {})
    if not isinstance(projects, dict):
        raise ValueError(f"Cannot safely inspect Claude local scopes at {user_config}.")
    local = projects.get(str(project), {})
    if not isinstance(local, dict):
        raise ValueError(f"Cannot safely inspect Claude local scope at {user_config}.")
    registrations = []
    # Claude selects one complete entry in this order, without merging fields.
    for scope, config in (("local", local), ("project", shared), ("user", user)):
        servers = config.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"Cannot safely inspect Claude {scope} scope: mcpServers must be an object.")
        if "hipson" in servers:
            registrations.append((scope, servers["hipson"]))
    return registrations


def build_plan(*, project_path: str, client: str, scope: str, dry_run: bool) -> InitPlan:
    report: dict[str, Any] = {
        "project": str(Path(project_path).expanduser().resolve()),
        "mode": "dry-run" if dry_run else "apply", "ok": False,
        "checks": {}, "actions": [], "verification": {"status": "skipped"},
        "next_steps": [], "errors": [],
    }
    plan = InitPlan(report)
    try:
        if client not in CLIENTS or scope not in ("project", "user"):
            raise ValueError("Invalid client or scope.")
        project = resolve_project(project_path)
        command = executable()
        db = ledger.default_ledger_db()
        version = _ledger_version(db)
        clients = {name: _client_executable(name) for name in ("claude", "codex")}
        report["checks"] = {
            "python_version": sys.version.split()[0], "package_version": __version__,
            "git": "ok" if shutil.which("git") else "missing",
            "platform": sys.platform, "platform_supported": sys.platform in ("linux", "darwin", "win32"),
            "hipson_executable": command, "ledger": str(db), "ledger_schema": version,
            "clients": clients,
        }
        actions = report["actions"]
        actions.append({
            "kind": "ledger", "path": str(db), "schema_version": ledger.SCHEMA_VERSION,
            "operation": "create" if version is None else "ensure/migrate",
            "status": "skipped" if version == ledger.SCHEMA_VERSION else "planned",
            "detail": "already configured" if version == ledger.SCHEMA_VERSION else f"initialize schema {ledger.SCHEMA_VERSION}",
        })
        server_args = ["mcp", "serve", "--project", str(project), "--stdio"]
        selected = [name for name, found in clients.items() if found] if client == "auto" else [client]
        if client == "auto" and not selected:
            raise ValueError("No Claude Code or Codex CLI found on PATH. Install a client, select --client codex, or use --client none for ledger-only setup.")
        for name in selected:
            if name == "claude":
                claude = clients[name]
                if claude is None:
                    raise ValueError("Claude Code is not on PATH (including claude.cmd on Windows).")
                probe = _run([claude, "mcp", "get", "hipson"], project)
                if probe.returncode != 0 and "no mcp server found" not in (probe.stdout + probe.stderr).lower():
                    raise ValueError(f"Claude MCP discovery failed (exit {probe.returncode}); run claude mcp get hipson to diagnose.")
                config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
                user_config = (Path(config_dir).expanduser().resolve() if config_dir else Path.home()) / ".claude.json"
                path = project / ".mcp.json" if scope == "project" else user_config
                registrations = _claude_registrations(plan, project, user_config)
                if probe.returncode == 0 and not registrations:
                    raise ValueError("Claude reports a hipson registration outside the inspected local/project/user config; resolve it with claude mcp get hipson before setup.")
                action: dict[str, Any] = {
                    "kind": "claude", "scope": scope, "path": str(path),
                    "command": [claude, "mcp", "add", "--transport", "stdio", "--scope", scope, "hipson", "--", command, *server_args],
                    "cwd": str(project), "status": "planned", "detail": "register stdio MCP server",
                }
                if registrations:
                    existing_scope, existing = registrations[0]
                    if existing_scope == scope and _registration_matches(existing, command, server_args, project):
                        action.update(status="skipped", detail="already configured")
                    else:
                        desired = {"command": command, "args": server_args}
                        conflict = (
                            f"Claude hipson registration conflict: existing {_registration_description(existing_scope, existing)}; "
                            f"desired {_registration_description(scope, desired)}."
                        )
                        if existing_scope == "user" and scope == "project":
                            action["detail"] = conflict + " Register a project-scope override; preserve the user registration."
                        else:
                            action.update(status="conflict", detail=conflict + " Remove or rename the conflicting registration explicitly before rerunning; setup never replaces it.")
                            report["errors"].append(action["detail"])
                actions.append(action)
            elif name == "codex":
                home, warnings = detect_codex_home()
                report["checks"]["warnings"] = warnings
                path = home / "config.toml"
                original = _snapshot(plan, path)
                try:
                    parsed = tomllib.loads((original or b"").decode("utf-8"))
                    servers = parsed.get("mcp_servers", {})
                    if not isinstance(servers, dict):
                        raise ValueError("mcp_servers must be a TOML table")
                    configured = "hipson" in servers
                    matches = configured and _registration_matches(servers["hipson"], command, server_args, project)
                    addition = ("\n" if original else "") + (
                        "\n[mcp_servers.hipson]\n"
                        f"command = {json.dumps(command, ensure_ascii=False)}\n"
                        f"args = {json.dumps(server_args, ensure_ascii=False)}\n"
                    )
                    if not configured:
                        tomllib.loads(((original or b"") + addition.encode("utf-8")).decode("utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise ValueError(f"Cannot safely append to Codex config at {path}: invalid or incompatible TOML.") from exc
                action = {
                    "kind": "codex", "scope": "user", "path": str(path),
                    "status": "skipped" if matches else "conflict" if configured else "planned",
                    "detail": "already configured" if matches else "append TOML (existing bytes preserved)",
                    "append": addition if not configured else "",
                }
                if configured and not matches:
                    desired = {"command": command, "args": server_args}
                    action["detail"] = (
                        f"Codex hipson registration conflict at {path}: existing {_registration_description('user', servers['hipson'])}; "
                        f"desired {_registration_description('user', desired)}. Command, args and enabled state must match. "
                        "Remove or rename the conflicting table explicitly before rerunning, or select a separate CODEX_HOME; setup never replaces it."
                    )
                    report["errors"].append(action["detail"])
                actions.append(action)
        for action in actions:
            path = Path(action["path"])
            if action["status"] == "planned" and path.exists():
                action["backup_before_edit"] = f"{path}.backup-YYYYMMDD-HHMMSS (exclusive; -N on collision)"
        report["verification"] = {
            "status": "planned", "command": [command, *server_args],
            "methods": ["initialize", "notifications/initialized", "tools/list"],
        }
        report["next_steps"] = [
            format_command(["hipson", "task", "open", "--project", str(project), "--title", "My first task", "--objective", "Describe the change", "--json"]),
            f"cd {shlex.quote(str(project))}" if sys.platform != "win32" else "Set-Location -LiteralPath '" + str(project).replace("'", "''") + "'",
            format_command(["hipson", "task", "run", "--task", "<task-id>", "--actor", "worker", "--role", "builder", "--mode", "rw", "--scope", ".", "--", "<worker-command>"]),
            format_command(["hipson", "task", "board"]),
        ]
        if report["checks"]["git"] != "ok":
            raise ValueError("Git is missing from PATH; install Git before setup.")
        if not report["checks"]["platform_supported"]:
            raise ValueError(f"Unsupported platform: {sys.platform}")
        report["ok"] = not report["errors"]
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError, SystemExit) as exc:
        report["errors"].append(redact_text(str(exc)))
    return plan


def apply_plan(plan: InitPlan) -> None:
    report = plan.report
    if not report["ok"] or report["mode"] == "dry-run":
        return
    project = Path(report["project"])
    action = None
    try:
        # Include discovery sources, not just edit targets: a new higher-priority
        # Claude entry could otherwise shadow the registration we are about to add.
        for path, original in plan.snapshots.items():
            if (path.read_bytes() if path.exists() else None) != original:
                raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.")
        for action in report["actions"]:
            if action["status"] != "planned":
                continue
            path = Path(action["path"])
            if action["kind"] == "ledger":
                if path.exists():
                    # A SQLite backup also includes committed frames in a live WAL.
                    backup = backup_file(path)
                    action["backup"] = str(backup)
                    with (
                        closing(ledger._sidecar_read_only_connection(path, time.monotonic() + TIMEOUT)) as source,
                        closing(sqlite3.connect(backup)) as destination,
                    ):
                        source.backup(destination)
                with closing(ledger.Ledger(path)):
                    pass
            else:
                current = path.read_bytes() if path.exists() else None
                if current != plan.snapshots[path]:
                    raise ValueError(f"Configuration changed since planning: {path}; rerun hipson init.")
                if current is not None:
                    action["backup"] = str(backup_file(path))
                if action["kind"] == "codex":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("ab" if current is not None else "xb") as destination_file:
                        destination_file.write(action["append"].encode("utf-8"))
                else:
                    result = _run(action["command"], project)
                    if result.returncode:
                        raise ValueError(f"Claude MCP registration failed (exit {result.returncode}); any backup is listed in actions.")
            action["status"] = "applied"
        report["verification"] = verify_mcp(report["verification"]["command"], project)
        report["ok"] = report["verification"]["status"] == "passed"
        if not report["ok"]:
            report["errors"].append(report["verification"]["detail"])
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        if action is not None:
            action["status"] = "failed"
        report["ok"] = False
        report["errors"].append(redact_text(str(exc)))


def verify_mcp(command: list[str], project: Path) -> dict[str, Any]:
    verification: dict[str, Any] = {"status": "failed", "command": command}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "hipson-init", "version": __version__},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        result = _run(command, project, input_text="".join(json.dumps(item) + "\n" for item in requests))
        verification["exit_code"] = result.returncode
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        by_id = {item.get("id"): item for item in responses if isinstance(item, dict)}
        initialized = by_id.get(1, {}).get("result", {})
        tools = by_id.get(2, {}).get("result", {}).get("tools", [])
        names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        if (
            result.returncode or any("error" in item for item in responses)
            or initialized.get("serverInfo", {}).get("name") != "hipson"
            or initialized.get("protocolVersion") != "2025-06-18"
            or not isinstance(initialized.get("capabilities", {}).get("tools"), dict)
            or not {"task.open", "task.board"} <= names
        ):
            raise ValueError("MCP initialize/tools/list did not return the Hipson task tools.")
        verification.update(status="passed", tools_count=len(tools), detail="initialize and tools/list passed")
    except (OSError, ValueError, AttributeError, TypeError, subprocess.SubprocessError) as exc:
        verification["detail"] = redact_text(f"MCP verification failed: {exc}")
    return verification


def format_report(report: dict[str, Any]) -> str:
    checks = report["checks"]
    lines = [f"Hipson init: {report['mode']}", f"Project: {report['project']}"]
    lines.extend(f"{name}: {value}" for name, value in checks.items() if name not in ("clients", "warnings"))
    lines.append("Plan:")
    for action in report["actions"]:
        lines.append(f"- [{action['status']}] {action['kind']}: {action['detail']} -> {action['path']}")
        if action.get("backup_before_edit"):
            lines.append(f"  Before edit: back up to {action['backup_before_edit']}.")
        if action.get("backup"):
            lines.append(f"  Backup: {action['backup']}")
        if action.get("command"):
            lines.append(f"  cwd: {action['cwd']}\n  {format_command(action['command'])}")
        if action.get("append"):
            lines.append("  Append exactly (UTF-8):\n" + action["append"])
    verification = report["verification"]
    lines.append(f"MCP verification: {verification['status']} (initialize + tools/list)")
    if verification.get("command"):
        lines.append("  " + format_command(verification["command"]))
    if report["next_steps"]:
        lines.append("Next steps (use the task id returned by task open):")
        lines.extend(f"  {step}" for step in report["next_steps"])
    lines.extend("Warning: " + warning for warning in checks.get("warnings", []))
    lines.extend("Error: " + error for error in report["errors"])
    return "\n".join(lines)
