"""Small stdlib-only tool registry for Hipson runtime tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

RiskLevel = Literal["read", "write", "external", "exec", "dangerous"]

TYPE_CHECKS: dict[str, type[object] | tuple[type[object], ...]] = {
    "str": str,
    "bool": bool,
    "int": int,
    "float": (int, float),
    "dict": dict,
    "list": list,
    "object": object,
}


class ToolRegistryError(ValueError):
    """Raised when a tool cannot be registered or executed."""


@dataclass(frozen=True)
class ToolContext:
    cwd: Path
    repo_root: Path | None
    session_id: str
    dry_run: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: dict[str, object]
    summary: str
    error: str = ""
    artifacts: tuple[str, ...] = ()
    redacted: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]
    output_contract: dict[str, object]
    risk_level: RiskLevel
    approval_required: bool
    handler: Callable[[dict[str, object], ToolContext], ToolResult]

    def __post_init__(self) -> None:
        _ensure_json_serializable(self.input_schema, f"{self.name} input schema")
        _ensure_json_serializable(self.output_contract, f"{self.name} output contract")


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolRegistryError(f"Duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolRegistryError(f"Unknown tool: {name}") from None

    def list(self) -> list[ToolSpec]:
        return [self._tools[name] for name in sorted(self._tools)]

    def run(self, name: str, input_data: dict[str, object], context: ToolContext) -> ToolResult:
        spec = self.get(name)
        _validate_input(spec, input_data)
        result = spec.handler(input_data, context)
        _ensure_json_serializable(result.output, f"{name} output")
        return result


def _validate_input(spec: ToolSpec, input_data: dict[str, object]) -> None:
    _ensure_json_serializable(input_data, f"{spec.name} input")
    required = _schema_fields(spec.input_schema, "required")
    optional = _schema_fields(spec.input_schema, "optional")
    allowed = set(required) | set(optional)
    missing = [name for name in required if name not in input_data]
    if missing:
        raise ToolRegistryError(f"{spec.name} missing required input: {', '.join(sorted(missing))}")
    unknown = [name for name in input_data if name not in allowed]
    if unknown:
        raise ToolRegistryError(f"{spec.name} unknown input: {', '.join(sorted(unknown))}")
    for field_name, type_name in {**required, **optional}.items():
        if field_name in input_data and not isinstance(input_data[field_name], _type_check(type_name)):
            raise ToolRegistryError(f"{spec.name} input {field_name} must be {type_name}")


def _schema_fields(schema: dict[str, object], key: str) -> dict[str, str]:
    value = schema.get(key, {})
    if not isinstance(value, dict):
        raise ToolRegistryError(f"Tool schema {key} must be an object")
    return {str(field_name): str(type_name) for field_name, type_name in value.items()}


def _type_check(type_name: str) -> type[object] | tuple[type[object], ...]:
    try:
        return TYPE_CHECKS[type_name]
    except KeyError:
        raise ToolRegistryError(f"Unsupported schema type: {type_name}") from None


def _ensure_json_serializable(value: object, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ToolRegistryError(f"{label} must be JSON-serializable") from exc


def build_default_registry() -> ToolRegistry:
    from hipson.tools.memory import register_memory_tools
    from hipson.tools.packets import register_packet_tools
    from hipson.tools.repo import register_repo_tools
    from hipson.tools.skills import register_skill_tools

    registry = ToolRegistry()
    register_repo_tools(registry)
    register_memory_tools(registry)
    register_packet_tools(registry)
    register_skill_tools(registry)
    return registry
