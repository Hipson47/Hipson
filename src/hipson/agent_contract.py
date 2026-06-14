"""First-class Hipson agent contract metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson import contracts
from hipson.project import resolve_project
from hipson.sandbox import ALLOWED_GENERATED_DIRS

ARTIFACT_KIND = "hipson.agent_contract"


def build_agent_contract(project_path: str | Path = ".") -> dict[str, Any]:
    project = resolve_project(str(project_path))
    return {
        "artifact_kind": ARTIFACT_KIND,
        "schema_version": contracts.SCHEMA_VERSION,
        "repo_state": contracts.repo_state(project),
        "supported_workflows": [
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
                "steps": ["evidence.show", "audit.show", "audit.export"],
                "provider_free_until": "always",
                "authoritative_evidence": ["evidence_ledger", "audit_bundle"],
            },
            {
                "name": "agent_autopilot_review",
                "steps": ["agent.bootstrap", "contract.show", "work", "packet.preflight", "verify.run", "quality.report", "evidence.append", "audit.show"],
                "provider_free_until": "sidecar.run",
                "authoritative_evidence": ["agent_contract", "project_policy", "local_verification", "audit_bundle"],
            },
            {
                "name": "agent_autopilot_implement",
                "steps": ["policy.validate", "work", "executor_packet", "packet.preflight", "verify.run", "quality.report", "evidence.append", "audit.show"],
                "provider_free_until": "sidecar.run",
                "authoritative_evidence": ["allowed_edit_scope", "local_verification", "human_decision"],
            },
            {
                "name": "mcp_read_first",
                "steps": ["initialize", "tools.list", "resources.list", "contract.show", "policy.show"],
                "provider_free_until": "always",
                "authoritative_evidence": ["tool_result", "resource_payload"],
            },
        ],
        "available_command_surfaces": {
            "local_core": ["contract show", "route", "scan", "work", "packet preflight", "verify run"],
            "agent_integration": ["install agents", "agent bootstrap", "doctor --agent-surfaces"],
            "autopilot": ["autopilot review", "autopilot implement", "autopilot resume"],
            "policy": ["policy show", "policy validate"],
            "mcp": ["mcp serve --catalog", "mcp serve --stdio"],
            "quality": ["quality report", "quality eval"],
            "audit": ["evidence append", "evidence show", "evidence export", "audit show", "audit export"],
            "provider_optional": ["sidecar route --llm", "sidecar run", "chat --provider"],
            "memory": ["memory add", "memory search", "memory list", "learn propose", "learn apply-memory"],
            "status_integration": ["hermes doctor", "hermes intake", "hermes events"],
        },
        "artifact_types": dict(sorted(contracts.ARTIFACT_SCHEMAS.items())),
        "risk_policy": {
            "levels": ["unknown", "normal", "ui", "architecture", "data-loss", "security"],
            "default_gate": "human review required for security, architecture, data-loss, UI review, and sidecar work",
            "gates": ["verification_gate", "sidecar_eval_gate", "human_decision_gate", "release_claim_gate"],
            "release_claim_rule": "Release claims require passed local verification, explicit human acceptance, and no unverified sidecar findings.",
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
            ],
            "provider_calls_are_explicit": True,
            "provider_surfaces": ["sidecar run", "sidecar route --llm", "chat --provider"],
            "free_model_policy": "Free models are advisory only and cannot approve work or release claims.",
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
                "capabilities": ["intake", "status", "telegram", "scheduler", "long_flow_coordination"],
            },
            "mcp_future": {
                "primary_interface": False,
                "capabilities": ["contract_read", "artifact_validation", "tool_surface_discovery", "audit_export"],
            },
            "mcp_stdio": {
                "primary_interface": False,
                "capabilities": ["initialize", "tools_list", "resources_list", "read_first_tools", "gated_verify", "gated_evidence"],
            },
        },
    }


def render_agent_contract(contract: dict[str, Any]) -> str:
    lines = [
        "# Hipson Agent Contract",
        "",
        f"- Schema version: `{contract['schema_version']}`",
        f"- Artifact kind: `{contract['artifact_kind']}`",
        "- Core workflow: `route -> scan -> packet/execute -> verify -> memory/handoff`",
        "- Provider policy: core contract/work/preflight/verify/evidence/audit commands are provider-free.",
        "- Release policy: release claims require local verification, human decision, and resolved sidecar findings.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def print_agent_contract(contract: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(contract, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_agent_contract(contract))
