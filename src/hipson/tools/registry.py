"""Small stdlib-only tool registry for Hipson runtime tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from json import JSONDecodeError
from pathlib import Path
from typing import Literal, cast

RiskLevel = Literal["read", "write", "external", "exec", "dangerous"]
PathPolicyMode = Literal["read_workspace", "write_generated", "read_memory_store", "read_skill_root", "read_skill_file"]
PATH_LIKE_FIELD_NAMES = frozenset({"path", "root", "output", "project", "packet", "source", "memory_dir", "cwd"})
MAX_PERSISTED_TOOL_OUTPUT_CHARS = 4_000
MAX_TOOL_OUTPUT_STRING_CHARS = 1_000
MAX_TOOL_OUTPUT_LIST_ITEMS = 20
MAX_TOOL_OUTPUT_DICT_KEYS = 30

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
class PathPolicy:
    field: str
    mode: PathPolicyMode
    base_field: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, object]
    output_contract: dict[str, object]
    risk_level: RiskLevel
    approval_required: bool
    handler: Callable[[dict[str, object], ToolContext], ToolResult]
    path_policies: tuple[PathPolicy, ...] = ()

    def __post_init__(self) -> None:
        _ensure_json_serializable(self.input_schema, f"{self.name} input schema")
        _ensure_json_serializable(self.output_contract, f"{self.name} output contract")
        _ensure_declared_path_policies(self)


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

    def validate_input(self, name: str, input_data: dict[str, object]) -> ToolSpec:
        spec = self.get(name)
        _validate_input(spec, input_data)
        return spec

    def run(self, name: str, input_data: dict[str, object], context: ToolContext) -> ToolResult:
        spec = self.get(name)
        _validate_input(spec, input_data)
        try:
            result = spec.handler(input_data, context)
        except (SystemExit, JSONDecodeError, ValueError, OSError, RuntimeError) as exc:
            return _handler_failure(name, exc)
        except Exception as exc:
            return _handler_failure(name, exc)
        try:
            _validate_output(spec, result)
        except ToolRegistryError as exc:
            return ToolResult(ok=False, output={}, summary=f"{name} output validation failed", error=str(exc))
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
        if field_name in input_data and not _matches_type(input_data[field_name], type_name):
            raise ToolRegistryError(f"{spec.name} input {field_name} must be {type_name}")


def _validate_output(spec: ToolSpec, result: ToolResult) -> None:
    _ensure_json_serializable(result.output, f"{spec.name} output")
    if not result.ok:
        return
    required, optional = _contract_fields(spec.output_contract)
    missing = [name for name in required if name not in result.output]
    if missing:
        raise ToolRegistryError(f"{spec.name} output missing required key: {', '.join(sorted(missing))}")
    allowed = set(required) | set(optional)
    unexpected = [name for name in result.output if allowed and name not in allowed]
    if unexpected:
        raise ToolRegistryError(f"{spec.name} output has unknown key: {', '.join(sorted(unexpected))}")
    for field_name, type_name in {**required, **optional}.items():
        if field_name in result.output and not _matches_type(result.output[field_name], type_name):
            raise ToolRegistryError(f"{spec.name} output {field_name} must be {type_name}")


def _schema_fields(schema: dict[str, object], key: str) -> dict[str, str]:
    value = schema.get(key, {})
    if not isinstance(value, dict):
        raise ToolRegistryError(f"Tool schema {key} must be an object")
    return {str(field_name): str(type_name) for field_name, type_name in value.items()}


def _contract_fields(contract: dict[str, object]) -> tuple[dict[str, str], dict[str, str]]:
    if "required" in contract or "optional" in contract:
        return _schema_fields(contract, "required"), _schema_fields(contract, "optional")
    return {str(field_name): str(type_name) for field_name, type_name in contract.items()}, {}


def _type_check(type_name: str) -> type[object] | tuple[type[object], ...]:
    try:
        return TYPE_CHECKS[type_name]
    except KeyError:
        raise ToolRegistryError(f"Unsupported schema type: {type_name}") from None


def _matches_type(value: object, type_name: str) -> bool:
    if "|" in type_name:
        return any(_matches_type(value, part.strip()) for part in type_name.split("|"))
    if type_name.endswith("|null"):
        return value is None or _matches_type(value, type_name.removesuffix("|null"))
    if type_name == "null":
        return value is None
    if type_name == "bool":
        return type(value) is bool
    if type_name == "int":
        return type(value) is int
    if type_name == "float":
        return type(value) in {int, float}
    if type_name.startswith("list[") and type_name.endswith("]"):
        if not isinstance(value, list):
            return False
        inner = type_name[5:-1]
        return all(_matches_type(item, inner) for item in value)
    if type_name == "object":
        return True
    if type_name in TYPE_CHECKS:
        return isinstance(value, _type_check(type_name))
    raise ToolRegistryError(f"Unsupported schema type: {type_name}")


def _ensure_json_serializable(value: object, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ToolRegistryError(f"{label} must be JSON-serializable") from exc


def _ensure_declared_path_policies(spec: ToolSpec) -> None:
    declared = {policy.field for policy in spec.path_policies}
    required = _schema_fields(spec.input_schema, "required")
    optional = _schema_fields(spec.input_schema, "optional")
    missing = sorted(field for field in set(required) | set(optional) if _is_path_like_field(field) and field not in declared)
    if missing:
        raise ToolRegistryError(f"{spec.name} path-like input needs path policy: {', '.join(missing)}")


def _is_path_like_field(field: str) -> bool:
    normalized = field.rsplit(".", 1)[-1].casefold()
    return normalized in PATH_LIKE_FIELD_NAMES or normalized.endswith("_path") or normalized.endswith("_dir")


def _handler_failure(name: str, exc: BaseException) -> ToolResult:
    from hipson.redaction import redact_text

    message = str(exc) or exc.__class__.__name__
    return ToolResult(ok=False, output={}, summary=f"{name} handler failed", error=redact_text(f"{exc.__class__.__name__}: {message}"))


def bounded_tool_output(result: ToolResult) -> dict[str, object]:
    bounded = _bound_value(result.output)
    if not isinstance(bounded, dict):
        bounded = {"value": bounded}
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= MAX_PERSISTED_TOOL_OUTPUT_CHARS:
        return bounded
    return {
        "summary": _bound_string(result.summary, MAX_TOOL_OUTPUT_STRING_CHARS),
        "artifacts": list(result.artifacts[:MAX_TOOL_OUTPUT_LIST_ITEMS]),
        "truncated": True,
    }


def _bound_value(value: object) -> object:
    if isinstance(value, str):
        return _bound_string(value, MAX_TOOL_OUTPUT_STRING_CHARS)
    if isinstance(value, list):
        list_items = [_bound_value(item) for item in value[:MAX_TOOL_OUTPUT_LIST_ITEMS]]
        if len(value) > MAX_TOOL_OUTPUT_LIST_ITEMS:
            list_items.append({"truncated_items": len(value) - MAX_TOOL_OUTPUT_LIST_ITEMS})
        return list_items
    if isinstance(value, dict):
        output: dict[str, object] = {}
        value_dict = cast(dict[object, object], value)
        dict_items = list(value_dict.items())
        for key, item in dict_items[:MAX_TOOL_OUTPUT_DICT_KEYS]:
            output[str(key)] = _bound_value(item)
        if len(dict_items) > MAX_TOOL_OUTPUT_DICT_KEYS:
            output["_truncated_keys"] = len(dict_items) - MAX_TOOL_OUTPUT_DICT_KEYS
        return output
    return value


def _bound_string(value: str, limit: int) -> str:
    from hipson.redaction import redact_text

    redacted = redact_text(value)
    if len(redacted) <= limit:
        return redacted
    marker = f"... [truncated to {limit} chars]"
    return redacted[: max(0, limit - len(marker))].rstrip() + marker


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
