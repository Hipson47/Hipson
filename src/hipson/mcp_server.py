"""Minimal MCP stdio/catalog surface for Hipson."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from hipson import agent_contract, evidence, packet_preflight, policy, quality, verification, workflow
from hipson.contracts import SCHEMA_VERSION
from hipson.project import resolve_project

MCP_TOOL_NAMES = (
    "contract.show",
    "policy.show",
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
        "status": "stdio_available",
        "server_mode": "catalog_or_stdio",
        "transport": "stdio",
        "stdio_server": True,
        "warnings": [],
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
        "input_schema": {"type": "object", "additionalProperties": True},
    }


def _description(name: str) -> str:
    descriptions = {
        "contract.show": "Read the local Hipson agent contract.",
        "policy.show": "Read the merged project policy.",
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
        "policy.show": "hipson policy show --json",
        "work.create": "hipson work --task \"...\" --write-packet --work-output runs/work.json --json",
        "packet.preflight": "hipson packet preflight runs/review-packet.md -o runs/preflight.json --json",
        "verify.run": "hipson verify run --work runs/work.json -o runs/verify.json --json",
        "quality.report": "hipson quality report --work runs/work.json --verify runs/verify.json -o runs/quality.json --json",
        "evidence.append": "hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json --json",
        "audit.show": "hipson audit show --work runs/work.json --json",
    }
    return commands[name]


def serve_stdio(
    *,
    project_path: str | Path = ".",
    stdin: TextIO,
    stdout: TextIO,
) -> None:
    """Serve a minimal line-delimited JSON-RPC MCP-compatible loop."""

    project = resolve_project(str(project_path))
    for line in stdin:
        if not line.strip():
            continue
        response = _handle_request(line, project=project)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        stdout.flush()


def _handle_request(line: str, *, project: Path) -> dict[str, Any] | None:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, -32700, f"Parse error: {exc}")
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid request")
    request_id = request.get("id")
    method = str(request.get("method", ""))
    params = request.get("params", {})
    if method.startswith("notifications/"):
        return None
    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "hipson", "version": SCHEMA_VERSION},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": [_mcp_tool_descriptor(name) for name in MCP_TOOL_NAMES]})
        if method == "resources/list":
            return _result(request_id, {"resources": server_catalog(project_path=project)["resources"]})
        if method == "resources/read":
            return _result(request_id, _read_resource(params, project=project))
        if method == "tools/call":
            return _result(request_id, _call_tool(params, project=project))
    except SystemExit as exc:
        return _tool_error(request_id, str(exc))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _tool_error(request_id, str(exc))
    return _error(request_id, -32601, f"Unknown method: {method}")


def _mcp_tool_descriptor(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": _description(name),
        "inputSchema": {"type": "object", "additionalProperties": True},
    }


def _read_resource(params: object, *, project: Path) -> dict[str, Any]:
    uri = str(params.get("uri", "")) if isinstance(params, dict) else ""
    if uri == "hipson://contract":
        payload = agent_contract.build_agent_contract(project)
    elif uri == "hipson://policy":
        payload = policy.load_policy(project)
    else:
        raise SystemExit(f"Unknown resource URI: {uri}")
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False)}]}


def _call_tool(params: object, *, project: Path) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise SystemExit("tools/call params must be an object")
    name = str(params.get("name", ""))
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        raise SystemExit("tools/call arguments must be an object")
    payload = _tool_payload(name, arguments, project=project)
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "isError": False}


def _tool_payload(name: str, arguments: dict[str, Any], *, project: Path) -> dict[str, Any]:
    if name == "contract.show":
        return agent_contract.build_agent_contract(arguments.get("project", project))
    if name == "policy.show":
        return policy.load_policy(arguments.get("project", project))
    if name == "work.create":
        task = str(arguments.get("task", "review current diff"))
        return dict(
            workflow.build_work_plan(
                task=task,
                project_path=str(arguments.get("project", project)),
                include_diff=bool(arguments.get("include_diff", True)),
                diff_lines=int(arguments.get("diff_lines", 120)),
                write_packet=False,
            )
        )
    if name == "packet.preflight":
        return packet_preflight.preflight_packet(arguments.get("path", ""))
    if name == "quality.report":
        return quality.build_quality_report(
            work_path=arguments.get("work", ""),
            verification_path=arguments.get("verify"),
            sidecar_path=arguments.get("sidecar"),
            decision=str(arguments.get("decision", "pending")),
        )
    if name == "audit.show":
        return evidence.audit_bundle(
            work_path=str(arguments.get("work", "")),
            ledger_root=Path(str(arguments.get("ledger_root", project / "runs"))).expanduser().resolve(),
        )
    if name == "verify.run":
        _require_mcp_approval(arguments, name)
        work_plan = verification.load_work_plan(arguments.get("work", ""))
        commands = verification.verification_commands(work_plan, limit=arguments.get("limit"))
        result = verification.run_verification(
            work_plan=work_plan,
            commands=commands,
            timeout=int(arguments.get("timeout", verification.DEFAULT_TIMEOUT)),
        )
        output = arguments.get("output") or verification.default_verification_output(work_plan)
        written = verification.write_verification_artifact(
            result,
            output,
            cwd=str(work_plan.get("project", "")) or None,
            allow_unsafe_output=bool(arguments.get("allow_unsafe_output", False)),
        )
        return {**result, "output": str(written)}
    if name == "evidence.append":
        _require_mcp_approval(arguments, name)
        work_plan = verification.load_work_plan(arguments.get("work", ""))
        ledger_root = evidence.evidence_dir(arguments.get("ledger_root"), project=work_plan.get("project"))
        verification_payload = evidence.load_json_artifact(arguments.get("verification"))
        quality_report_payload = evidence.load_json_artifact(arguments.get("quality_report"))
        quality_eval_payload = evidence.load_json_artifact(arguments.get("quality_eval"))
        record = evidence.build_evidence_record(
            work_plan=work_plan,
            verification=verification_payload,
            quality_report=quality_report_payload,
            quality_eval=quality_eval_payload,
            sidecar_report=arguments.get("sidecar_report", ""),
            ledger_root=ledger_root,
            human_decision=str(arguments.get("decision", "pending")),
        )
        path = evidence.append_record(ledger_root, record)
        return {"ledger": str(path), "record": record}
    raise SystemExit(f"Unknown tool: {name}")


def _require_mcp_approval(arguments: dict[str, Any], tool_name: str) -> None:
    if arguments.get("approved") is not True:
        raise SystemExit(f"{tool_name} requires approved=true for MCP write/gated execution")


def _tool_error(request_id: object, message: str) -> dict[str, Any]:
    return _result(request_id, {"content": [{"type": "text", "text": message}], "isError": True})


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
