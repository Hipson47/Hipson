"""First-class Hipson agent contract metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson import contracts
from hipson.project import resolve_project
from hipson.sandbox import ALLOWED_GENERATED_DIRS

ARTIFACT_KIND = "hipson.agent_contract"

# Experimental surfaces hidden since 1.7: still callable for existing scripts, absent from help, the quick
# reference, MCP and this contract, and removed in 2.0. docs/DEPRECATIONS.md explains each replacement.
DEPRECATED_COMMANDS = {
    "autopilot": "use hipson kit review, or hipson task do for ledger-tracked work",
    "release": "a release claim is not a release gate; close work with hipson task close",
    "scheduler": "run Hipson from your own scheduler; jobs bind no workspace and have no atomic claim",
    "chat": "use a coding agent with the hipson MCP server",
    "learn": "record durable lessons with hipson memory add",
    "hermes": "the Hermes bridge stays only for existing integrations",
}


def build_agent_contract(project_path: str | Path = ".") -> dict[str, Any]:
    project = resolve_project(str(project_path))
    return {
        "artifact_kind": ARTIFACT_KIND,
        "schema_version": contracts.SCHEMA_VERSION,
        "repo_state": contracts.repo_state(project),
        "supported_workflows": [
            {
                "name": "lead_task_ledger",
                "steps": ["lead.resume", "task.open --check --start", "task run", "task.now", "task.ack", "task.close"],
                "provider_free_until": "always",
                "authoritative_evidence": ["declared_acceptance_checks", "lead_verification", "ledger_events",
                                           "acknowledged_results"],
            },
            {
                "name": "codex_daily_work",
                "steps": ["route", "scan", "packet_or_execute", "verify", "memory", "handoff"],
                "provider_free_until": "sidecar.run",
                "authoritative_evidence": ["git_diff", "local_commands", "evidence_ledger", "human_decision"],
            },
            {
                "name": "advisory_ai_quality",
                "steps": ["packet_preflight", "sidecar.run", "quality.report", "quality.eval", "evidence.append"],
                "provider_free_until": "sidecar.run",
                "authoritative_evidence": ["local_verification", "human_decision"],
            },
            {
                "name": "audit_handoff",
                "steps": ["evidence.show", "audit.show", "run.status", "run.validate", "run.handoff", "audit.export"],
                "provider_free_until": "always",
                "authoritative_evidence": ["evidence_ledger", "audit_bundle", "run_manifest", "agent_handoff"],
            },
            {
                "name": "kit_review",
                "steps": ["kit review", "packet.preflight", "verify.run", "quality.report", "evidence.append", "audit.show"],
                "provider_free_until": "sidecar.run",
                "authoritative_evidence": ["project_policy", "local_verification", "audit_bundle"],
            },
            {
                "name": "mcp_read_first",
                "steps": ["initialize", "tools.list", "lead.resume", "task.now", "contract.show", "policy.show"],
                "provider_free_until": "always",
                "authoritative_evidence": ["tool_result", "resource_payload"],
            },
        ],
        "available_command_surfaces": {
            "ledger": ["task open", "task run", "task now", "task ack", "task verify", "task close", "task do",
                       "task finding", "lead resume"],
            "local_core": ["contract show", "route", "scan", "work", "packet preflight", "verify run"],
            "agent_integration": ["install agents", "agent bootstrap", "doctor --agent-surfaces"],
            "policy": ["policy show", "policy validate"],
            "mcp": ["mcp serve --catalog", "mcp serve --stdio"],
            "run_control": ["run status", "run validate", "run handoff"],
            'lead_visibility': ['task now', 'lead resume'],
            "quality": ["quality report", "quality eval"],
            "audit": ["evidence append", "evidence show", "evidence export", "audit show", "audit export"],
            "provider_optional": ["sidecar route --llm", "sidecar run"],
            "memory": ["memory add", "memory search", "memory list"],
        },
        "deprecated_command_surfaces": dict(sorted(DEPRECATED_COMMANDS.items())),
        "artifact_types": dict(sorted(contracts.ARTIFACT_SCHEMAS.items())),
        "risk_policy": {
            "levels": ["unknown", "normal", "ui", "architecture", "data-loss", "security"],
            "default_gate": "human review required for security, architecture, data-loss, UI review, and sidecar work",
            "gates": ["verification_gate", "sidecar_eval_gate", "human_decision_gate", "release_claim_gate"],
            "completion_rule": "A ledger task reaches review/done only with a fresh passing lead verification of every declared acceptance check, acknowledged results and no open blocking/major findings.",
        },
        "path_write_policy": {
            "default": "Generated artifacts must stay under an allowed generated directory unless explicitly overridden.",
            "allowed_generated_dirs": sorted(ALLOWED_GENERATED_DIRS),
            "blocked": ["path traversal", "broad home/profile paths", "sensitive paths such as .env, keys, db files"],
            "explicit_override": "--allow-unsafe-output",
            "applies_to": [
                "--packet-output",
                "--work-output",
                "packet preflight output",
                "verification output",
                "quality report output",
                "quality eval output",
                "evidence export output",
                "audit export output",
                "run handoff output",
            ],
        },
        "provider_policy": {
            "core_commands_provider_free": [
                "contract",
                "work",
                "packet preflight",
                "verify",
                "quality report",
                "quality eval",
                "evidence",
                "audit",
                "run",
                "task",
            ],
            "provider_calls_are_explicit": True,
            "provider_surfaces": ["sidecar run", "sidecar route --llm"],
            "free_model_policy": "Free models are advisory only and cannot approve or close work.",
        },
        "memory_policy": {
            "default_scope": "repo",
            "stored_content": "redacted summaries, decisions, handoffs, source references",
            "blocked": ["raw secrets", "sensitive source paths"],
            "human_apply_required": True,
        },
        "verification_policy": {
            "local_first": True,
            "shell_control_syntax_blocked": True,
            "verification_gate": "passed only when selected local commands exit 0",
            "sidecar_findings": "advisory until checked against local files, tests, and human review",
        },
        "adapter_capabilities": {
            "codex": {
                "primary_interface": True,
                "capabilities": ["route", "scan", "packet", "execute", "verify", "memory", "audit"],
            },
            "hermes": {
                "primary_interface": False,
                "deprecated": True,
                "capabilities": ["intake", "status", "telegram", "scheduler", "long_flow_coordination"],
            },
            "mcp_future": {
                "primary_interface": False,
                "capabilities": ["contract_read", "artifact_validation", "tool_surface_discovery", "audit_export"],
            },
            "mcp_stdio": {
                "primary_interface": True,
                "capabilities": [
                    "initialize",
                    "tools_list",
                    "resources_list",
                    "read_first_tools",
                    "run_status",
                    "run_validation",
                    "gated_verify",
                    "gated_evidence",
                    "gated_handoff",
                    "task_show",
                    "task_brief",
                    "task_context",
                    'task_activity',
                    "task_events",
                    "task_list",
                    "task_board",
                    'task_now',
                    'lead_resume',
                    "task_findings",
                    "gated_task_finding_add",
                    "gated_task_finding_update",
                    "gated_task_open",
                    "gated_task_assign",
                    "gated_task_finish",
                    'gated_task_ack',
                    "gated_task_reap",
                    "gated_task_event",
                    "gated_task_status",
                    "gated_task_verify",
                    "gated_task_checks",
                    "gated_task_close",
                ],
            },
        },
    }


def render_agent_contract(contract: dict[str, Any]) -> str:
    lines = [
        "# Hipson Agent Contract",
        "",
        f"- Schema version: `{contract['schema_version']}`",
        f"- Artifact kind: `{contract['artifact_kind']}`",
        "- Lead workflow: `lead resume -> task open --check --start -> task run -> task now/ack -> task close`",
        "- Completion: review/done need a fresh passing lead verification of every declared acceptance check.",
        "- Provider policy: core contract/work/preflight/verify/evidence/audit/task commands are provider-free.",
        "- Deprecated (hidden, removed in 2.0): " + ", ".join(sorted(contract.get("deprecated_command_surfaces", {}))),
    ]
    return "\n".join(lines).rstrip() + "\n"


def print_agent_contract(contract: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_agent_contract(contract))
