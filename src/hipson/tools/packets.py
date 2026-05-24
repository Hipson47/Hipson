"""Packet-generation tools wrapping existing Hipson packet compilers."""

from __future__ import annotations

from pathlib import Path

from hipson import project as hipson_project
from hipson.packets import compile_review_packet
from hipson.redaction import redact_text
from hipson.tools.registry import PathPolicy, ToolContext, ToolRegistry, ToolResult, ToolSpec

ALLOWED_OUTPUT_DIRS = {"runs", "scans", "docs"}


def register_packet_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="packet.review.create",
            description="Create a bounded read-only review packet under an allowed generated/docs path.",
            input_schema={
                "required": {"project": "str", "title": "str"},
                "optional": {"scope": "str", "include_diff": "bool", "output": "str"},
            },
            output_contract={"path": "str", "summary": "str"},
            risk_level="write",
            approval_required=False,
            handler=packet_review_create,
            path_policies=(
                PathPolicy("project", "read_workspace"),
                PathPolicy("output", "write_generated"),
            ),
        )
    )


def packet_review_create(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    project = _resolve_project(str(input_data["project"]), context)
    scope = str(input_data.get("scope", "current git delta"))
    include_diff = bool(input_data.get("include_diff", False))
    output_path = _resolve_output_path(str(input_data.get("output", "runs/review-packet.md")), context.cwd)
    if output_path is None:
        return ToolResult(
            ok=False,
            output={},
            summary="Review packet was not written",
            error="Output path must stay under runs/, scans/, or docs/ inside the current workspace.",
        )

    scan = hipson_project.build_scan(project, include_diff=include_diff, diff_lines=3)
    root = hipson_project.git_root(project)
    text = compile_review_packet(
        title=str(input_data["title"]),
        project=str(project),
        scope=scope,
        scan=scan,
        changed_files=hipson_project.changed_files(project, root),
        commands=hipson_project.discover_commands(project),
        selected_skills=[],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(redact_text(text), encoding="utf-8")
    summary = f"Created review packet at {output_path}"
    return ToolResult(ok=True, output={"path": str(output_path), "summary": summary}, summary=summary, artifacts=(str(output_path),))


def _resolve_project(path: str, context: ToolContext) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = context.cwd / candidate
    return hipson_project.resolve_project(str(candidate))


def _resolve_output_path(path: str, cwd: Path) -> Path | None:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else cwd / raw
    resolved = candidate.resolve()
    workspace = cwd.resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] not in ALLOWED_OUTPUT_DIRS:
        return None
    return resolved
