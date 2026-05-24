"""Approval policy skeleton for Hipson runtime tool execution."""

from __future__ import annotations

from dataclasses import dataclass

from hipson.sandbox import (
    SandboxDecision,
    check_read_path,
    check_skill_file_path,
    check_skill_root_path,
    check_write_path,
    is_allowlisted_read_only_command,
)
from hipson.tools.registry import RiskLevel, ToolContext, ToolSpec


@dataclass(frozen=True)
class ApprovalDecision:
    allowed: bool
    requires_approval: bool
    blocked: bool
    risk_level: RiskLevel
    reason: str

    def to_metadata(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "blocked": self.blocked,
            "risk_level": self.risk_level,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ApprovalPolicy:
    def evaluate_tool(
        self,
        spec: ToolSpec,
        input_data: dict[str, object],
        context: ToolContext,
        *,
        approved: bool = False,
        fake_provider: bool = False,
        dry_run: bool | None = None,
    ) -> ApprovalDecision:
        path_decision = _check_tool_path_policies(spec, input_data, context)
        if path_decision is not None:
            return path_decision
        return self._evaluate_risk(
            spec.risk_level,
            input_data,
            context,
            approved=approved,
            fake_provider=fake_provider,
            dry_run=dry_run,
            check_legacy_paths=False,
        )

    def evaluate(
        self,
        risk_level: RiskLevel,
        input_data: dict[str, object],
        context: ToolContext,
        *,
        approved: bool = False,
        fake_provider: bool = False,
        dry_run: bool | None = None,
    ) -> ApprovalDecision:
        return self._evaluate_risk(
            risk_level,
            input_data,
            context,
            approved=approved,
            fake_provider=fake_provider,
            dry_run=dry_run,
            check_legacy_paths=True,
        )

    def _evaluate_risk(
        self,
        risk_level: RiskLevel,
        input_data: dict[str, object],
        context: ToolContext,
        *,
        approved: bool,
        fake_provider: bool,
        dry_run: bool | None,
        check_legacy_paths: bool,
    ) -> ApprovalDecision:
        effective_dry_run = context.dry_run if dry_run is None else dry_run
        if risk_level == "dangerous":
            return _blocked(risk_level, "Dangerous actions are blocked by default")
        if check_legacy_paths:
            path_decision = _check_input_paths(input_data, context, risk_level)
            if path_decision is not None:
                return path_decision
        if risk_level == "read":
            return _allowed(risk_level, "Read allowed after sandbox checks")
        if risk_level == "write":
            return _write_decision(input_data, context, approved)
        if risk_level == "external":
            if effective_dry_run or fake_provider or approved:
                return _allowed(risk_level, "External action allowed by dry-run, fake provider, or approval")
            return _requires_approval(risk_level, "External actions require explicit approval")
        if risk_level == "exec":
            command = _command(input_data)
            if command and is_allowlisted_read_only_command(command):
                return _allowed(risk_level, "Allowlisted read-only command")
            if approved:
                return _allowed(risk_level, "Exec action allowed by explicit approval")
            return _requires_approval(risk_level, "Exec actions require explicit approval unless allowlisted")
        return _blocked(risk_level, f"Unsupported risk level: {risk_level}")


def _check_tool_path_policies(
    spec: ToolSpec,
    input_data: dict[str, object],
    context: ToolContext,
) -> ApprovalDecision | None:
    for policy in spec.path_policies:
        value = _field_value(input_data, policy.field)
        if value is None:
            continue
        if not isinstance(value, str):
            return _blocked(spec.risk_level, f"{policy.field} path value must be a string")
        decision = _path_policy_decision(policy.mode, value, policy.base_field, input_data, context)
        if not decision.allowed:
            return _blocked(spec.risk_level, decision.reason)
    return None


def _field_value(data: dict[str, object], field: str) -> object:
    value: object = data
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _path_policy_decision(
    mode: str,
    value: str,
    base_field: str,
    input_data: dict[str, object],
    context: ToolContext,
) -> SandboxDecision:
    if mode in {"read_workspace", "read_memory_store"}:
        return check_read_path(value, context.cwd)
    if mode == "write_generated":
        return check_write_path(value, context.cwd)
    if mode == "read_skill_root":
        return check_skill_root_path(value, context.cwd)
    if mode == "read_skill_file":
        root = _field_value(input_data, base_field) if base_field else None
        return check_skill_file_path(value, root if isinstance(root, str) else None, context.cwd)
    return check_read_path(value, context.cwd)


def _check_input_paths(
    input_data: dict[str, object],
    context: ToolContext,
    risk_level: RiskLevel,
) -> ApprovalDecision | None:
    for key in ("path", "project", "packet", "source"):
        value = input_data.get(key)
        if isinstance(value, str):
            decision = check_read_path(value, context.cwd)
            if not decision.allowed:
                return _blocked(risk_level, decision.reason)
    if risk_level == "write":
        output = input_data.get("output")
        if isinstance(output, str):
            path_decision = _write_path_decision(output, context)
            if path_decision is not None:
                return path_decision
    return None


def _write_decision(input_data: dict[str, object], context: ToolContext, approved: bool) -> ApprovalDecision:
    output = input_data.get("output")
    if isinstance(output, str):
        path_decision = _write_path_decision(output, context)
        if path_decision is None:
            return _allowed("write", "Write allowed inside generated/docs path")
        return path_decision
    if approved:
        return _allowed("write", "Write action allowed by explicit approval")
    return _requires_approval("write", "Write actions require generated/docs paths or explicit approval")


def _write_path_decision(output: str, context: ToolContext) -> ApprovalDecision | None:
    decision = check_write_path(output, context.cwd)
    if decision.allowed:
        return None
    if decision.reason == "Write path must be under runs/, scans/, docs/, or memory/":
        return _requires_approval("write", decision.reason)
    return _blocked("write", decision.reason)


def _command(input_data: dict[str, object]) -> list[str]:
    value = input_data.get("cmd", input_data.get("command"))
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return []


def _allowed(risk_level: RiskLevel, reason: str) -> ApprovalDecision:
    return ApprovalDecision(True, False, False, risk_level, reason)


def _requires_approval(risk_level: RiskLevel, reason: str) -> ApprovalDecision:
    return ApprovalDecision(False, True, False, risk_level, reason)


def _blocked(risk_level: RiskLevel, reason: str) -> ApprovalDecision:
    return ApprovalDecision(False, False, True, risk_level, reason)
