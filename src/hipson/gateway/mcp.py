"""Optional MCP-style bridge over Hipson's internal tool registry.

This module intentionally avoids an MCP runtime dependency. It exposes a small
adapter surface that future protocol glue can call without bypassing Hipson's
registry and approval policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hipson.approvals import ApprovalPolicy
from hipson.redaction import redact_text
from hipson.tools import (
    ToolContext,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    bounded_tool_output,
    build_default_registry,
)


@dataclass
class MCPBridge:
    registry: ToolRegistry = field(default_factory=build_default_registry)
    approval_policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)

    def list_tools(self, *, include_approval_required: bool = False) -> list[dict[str, object]]:
        specs = self.registry.list()
        if not include_approval_required:
            specs = [spec for spec in specs if _is_safe_listed(spec)]
        return [_tool_descriptor(spec) for spec in specs]

    def call_tool(
        self,
        name: str,
        input_data: dict[str, object],
        *,
        cwd: Path,
        approved: bool = False,
    ) -> dict[str, object]:
        try:
            spec = self.registry.get(name)
            self.registry.validate_input(name, input_data)
        except ToolRegistryError as exc:
            return _rejected("rejected", str(exc))

        context = ToolContext(cwd=cwd.resolve(), repo_root=None, session_id="mcp")
        decision = self.approval_policy.evaluate_tool(spec, input_data, context, approved=approved)
        if not decision.allowed:
            status = "blocked" if decision.blocked else "approval_required"
            return _rejected(status, decision.reason)
        if not _is_safe_listed(spec):
            return _rejected("approval_required", "MCP bridge exposes non-read tools only after explicit protocol approval")

        try:
            result = self.registry.run(name, input_data, context)
        except ToolRegistryError as exc:
            return _rejected("rejected", str(exc))

        if not result.ok:
            return {
                "ok": False,
                "status": "failed",
                "output": _redact_value(bounded_tool_output(result)),
                "summary": redact_text(result.summary),
                "error": redact_text(result.error),
            }
        return {
            "ok": True,
            "status": "completed",
            "output": _redact_value(bounded_tool_output(result)),
            "summary": redact_text(result.summary),
            "error": "",
        }


def _is_safe_listed(spec: ToolSpec) -> bool:
    return spec.risk_level == "read" and not spec.approval_required


def _tool_descriptor(spec: ToolSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "output_contract": spec.output_contract,
        "risk_level": spec.risk_level,
        "approval_required": spec.approval_required,
    }


def _rejected(status: str, error: str) -> dict[str, object]:
    return {
        "ok": False,
        "status": status,
        "output": {},
        "summary": "Tool call rejected",
        "error": redact_text(error),
    }


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value
