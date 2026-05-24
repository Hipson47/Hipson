"""Repository-state tools wrapping existing Hipson scan helpers."""

from __future__ import annotations

from pathlib import Path

from hipson import project as hipson_project
from hipson.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


def register_repo_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="repo.scan",
            description="Scan a local repository and return a redacted Hipson delta scan.",
            input_schema={
                "required": {"path": "str"},
                "optional": {"include_diff": "bool", "diff_lines": "int"},
            },
            output_contract={
                "markdown": "str",
                "changed_files": "list[str]",
                "commands": "list[str]",
                "artifact": "str|null",
            },
            risk_level="read",
            approval_required=False,
            handler=repo_scan,
        )
    )
    registry.register(
        ToolSpec(
            name="repo.changed_files",
            description="List changed and untracked files for a local repository.",
            input_schema={"required": {"path": "str"}, "optional": {}},
            output_contract={"changed_files": "list[str]", "untracked_files": "list[str]"},
            risk_level="read",
            approval_required=False,
            handler=repo_changed_files,
        )
    )


def repo_scan(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    project = _resolve_input_path(str(input_data["path"]), context)
    include_diff = bool(input_data.get("include_diff", False))
    diff_lines = _int_value(input_data.get("diff_lines"), default=3)
    try:
        scan = hipson_project.build_scan(project, include_diff=include_diff, diff_lines=diff_lines)
        root = hipson_project.git_root(project)
        output: dict[str, object] = {
            "markdown": scan,
            "changed_files": hipson_project.changed_files(project, root),
            "commands": hipson_project.discover_commands(project),
            "artifact": None,
        }
        return ToolResult(ok=True, output=output, summary=f"Scanned {project}")
    except SystemExit as exc:
        return ToolResult(ok=False, output={}, summary="Repo scan failed", error=str(exc))


def repo_changed_files(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    project = _resolve_input_path(str(input_data["path"]), context)
    root = hipson_project.git_root(project)
    output: dict[str, object] = {
        "changed_files": hipson_project.changed_files(project, root),
        "untracked_files": hipson_project.untracked_files(project, root),
    }
    return ToolResult(ok=True, output=output, summary=f"Listed changed files for {project}")


def _resolve_input_path(path: str, context: ToolContext) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = context.cwd / candidate
    return hipson_project.resolve_project(str(candidate))


def _int_value(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(str(value))
