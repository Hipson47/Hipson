"""Read-first MCP server skeleton for Hipson."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hipson.contracts import SCHEMA_VERSION
from hipson.project import resolve_project

MCP_TOOL_NAMES = (
    "contract.show",
    "work.create",
    "packet.preflight",
    "verify.run",
    "quality.report",
    "evidence.append",
    "audit.show",
)


def server_catalog(*, project_path: str | Path = ".") -> dict[str, Any]:
    project = resolve_project(str(project_path))
    return {
        "artifact_kind": "hipson.mcp_server_catalog",
        "schema_version": SCHEMA_VERSION,
        "project": str(project),
        "status": "catalog_only",
        "server_mode": "catalog_only",
        "transport": "stdio",
        "stdio_server": False,
        "warnings": [
            "This PR exposes an MCP catalog only; a protocol-complete stdio MCP server is not enabled yet."
        ],
        "provider_policy": {
            "default": "provider_free",
            "hidden_provider_calls": False,
            "provider_calls_require": ["explicit_sidecar_command", "--run-sidecar"],
        },
        "tools": [_tool(name) for name in MCP_TOOL_NAMES],
        "resources": [
            {
                "uri": "hipson://contract",
                "name": "Agent Contract",
                "command": "hipson contract show --json",
                "read_only": True,
            },
            {
                "uri": "hipson://policy",
                "name": "Project Policy",
                "command": "hipson policy show --json",
                "read_only": True,
            },
            {
                "uri": "hipson://latest-audit",
                "name": "Latest Audit Bundle",
                "command": "hipson audit show --work runs/<work_id>/work.json --json",
                "read_only": True,
            },
        ],
    }


def _tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "read_first": True,
        "provider_free": True,
        "approval_required": name in {"verify.run", "evidence.append"},
        "description": _description(name),
        "fallback_command": _fallback_command(name),
    }


def _description(name: str) -> str:
    descriptions = {
        "contract.show": "Read the local Hipson agent contract.",
        "work.create": "Create a provider-free work plan and optional packet path.",
        "packet.preflight": "Validate a bounded packet before any provider-backed sidecar.",
        "verify.run": "Run selected local verification commands and record bounded output.",
        "quality.report": "Correlate work, verification, and optional sidecar artifacts.",
        "evidence.append": "Append a local evidence record to the run ledger.",
        "audit.show": "Read the audit bundle for handoff and release gate status.",
    }
    return descriptions[name]


def _fallback_command(name: str) -> str:
    commands = {
        "contract.show": "hipson contract show --json",
        "work.create": "hipson work --task \"...\" --write-packet --work-output runs/work.json --json",
        "packet.preflight": "hipson packet preflight runs/review-packet.md -o runs/preflight.json --json",
        "verify.run": "hipson verify run --work runs/work.json -o runs/verify.json --json",
        "quality.report": "hipson quality report --work runs/work.json --verify runs/verify.json -o runs/quality.json --json",
        "evidence.append": "hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json --json",
        "audit.show": "hipson audit show --work runs/work.json --json",
    }
    return commands[name]
