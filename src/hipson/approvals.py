"""Approval policy skeleton for Hipson runtime tool execution."""

from __future__ import annotations

from dataclasses import dataclass

from hipson.sandbox import check_read_path, check_write_path, is_allowlisted_read_only_command
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
        return self.evaluate(
            spec.risk_level,
            input_data,
            context,
            approved=approved,
            fake_provider=fake_provider,
            dry_run=dry_run,
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
        effective_dry_run = context.dry_run if dry_run is None else dry_run
        if risk_level == "dangerous":
            return _blocked(risk_level, "Dangerous actions are blocked by default")
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
