"""Skill reference tools for the Hipson runtime."""

from __future__ import annotations

from pathlib import Path

from hipson.paths import package_root
from hipson.skills import SkillLookupError, list_skill_metadata, view_skill
from hipson.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

DEFAULT_SKILL_ROOT = package_root()


def register_skill_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="skill.list",
            description="List available skill metadata without loading full skill text.",
            input_schema={"required": {}, "optional": {"root": "str", "query": "str"}},
            output_contract={"skills": "list[dict]"},
            risk_level="read",
            approval_required=False,
            handler=skill_list,
        )
    )
    registry.register(
        ToolSpec(
            name="skill.view",
            description="View one bounded skill as untrusted reference data.",
            input_schema={"required": {}, "optional": {"root": "str", "name": "str", "path": "str", "max_chars": "int"}},
            output_contract={
                "name": "str",
                "description": "str",
                "path": "str",
                "content": "str",
                "truncated": "bool",
            },
            risk_level="read",
            approval_required=False,
            handler=skill_view,
        )
    )


def skill_list(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    root = _resolve_root(input_data.get("root"), context)
    if root is None:
        return ToolResult(ok=False, output={"skills": []}, summary="Skill root was rejected", error="Invalid skill root")
    query = str(input_data.get("query", ""))
    skills = list_skill_metadata(root, query=query)
    return ToolResult(ok=True, output={"skills": skills}, summary=f"Listed {len(skills)} skill(s)")


def skill_view(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    root = _resolve_root(input_data.get("root"), context)
    if root is None:
        return ToolResult(ok=False, output={}, summary="Skill root was rejected", error="Invalid skill root")
    max_chars = _int_value(input_data.get("max_chars"), default=4000)
    try:
        output = view_skill(
            root,
            name=_optional_str(input_data.get("name")),
            path=_optional_str(input_data.get("path")),
            max_chars=max_chars,
        )
    except SkillLookupError as exc:
        return ToolResult(ok=False, output={}, summary="Skill not found", error=str(exc))
    return ToolResult(ok=True, output=output, summary=f"Viewed skill {output['name']}")


def _resolve_root(value: object, context: ToolContext) -> Path | None:
    if value is None or value == "":
        return DEFAULT_SKILL_ROOT
    if not isinstance(value, str):
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = context.cwd / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(context.cwd.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_value(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(str(value))
