"""Memory tools wrapping the existing JSONL memory module."""

from __future__ import annotations

from pathlib import Path

from hipson import memory as hipson_memory
from hipson.tools.registry import PathPolicy, ToolContext, ToolRegistry, ToolResult, ToolSpec


def register_memory_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="memory.search",
            description="Search local Hipson JSONL memory for compact prior decisions and handoffs.",
            input_schema={
                "required": {"query": "str"},
                "optional": {"repo": "str", "scope": "str", "limit": "int", "memory_dir": "str"},
            },
            output_contract={"results": "list[dict]"},
            risk_level="read",
            approval_required=False,
            handler=memory_search,
            path_policies=(PathPolicy("memory_dir", "read_memory_store"),),
        )
    )


def memory_search(input_data: dict[str, object], context: ToolContext) -> ToolResult:
    root = _memory_root(input_data, context)
    results = hipson_memory.search_notes(
        root=root,
        query=str(input_data["query"]),
        repo=_optional_str(input_data.get("repo")),
        scope=_optional_str(input_data.get("scope")),
        limit=_int_value(input_data.get("limit"), default=5),
    )
    output: dict[str, object] = {
        "results": [
            {
                "id": result.note.id,
                "scope": result.note.scope,
                "repo": result.note.repo,
                "kind": result.note.kind,
                "summary": result.note.summary,
                "source_refs": result.note.source_refs,
                "tags": result.note.tags,
                "confidence": result.note.confidence,
                "score": result.score,
            }
            for result in results
        ]
    }
    return ToolResult(ok=True, output=output, summary=f"Found {len(results)} memory result(s)")


def _memory_root(input_data: dict[str, object], context: ToolContext) -> Path:
    raw = input_data.get("memory_dir")
    if raw:
        path = Path(str(raw)).expanduser()
        return path if path.is_absolute() else context.cwd / path
    return context.cwd / "memory"


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _int_value(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(str(value))
