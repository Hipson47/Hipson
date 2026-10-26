"""Minimal MCP stdio/catalog surface for Hipson."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, TextIO

from hipson import (
    agent_contract,
    evidence,
    lead_context,
    ledger,
    packet_preflight,
    policy,
    quality,
    run_control,
    task_context,
    verification,
    workflow,
)
from hipson.contracts import SCHEMA_VERSION
from hipson.project import resolve_project
from hipson.redaction import redact_text

MCP_TOOL_NAMES = (
    'task.now',
    'lead.resume',
    "contract.show",
    "policy.show",
    "work.create",
    "packet.preflight",
    "verify.run",
    "quality.report",
    "evidence.append",
    "audit.show",
    "run.status",
    "run.validate",
    "handoff.create",
    "release.claim",
    "task.show",
    "task.brief",
    "task.context",
    "task.events",
    "task.list",
    "task.board",
    "task.findings",
    "task.finding_add",
    "task.finding_update",
    "task.open",
    "task.assign",
    "task.finish",
    "task.activity",
    "task.ack",
    "task.reap",
    "task.event",
    "task.status",
)

MCP_GATED_TOOLS = {
    "verify.run", "evidence.append", "handoff.create", "release.claim",
    "task.open", "task.assign", "task.finish", "task.reap", "task.event", "task.status",
    "task.finding_add", "task.finding_update", "task.ack",
}
MCP_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    'task.now': {
        'type': 'object', 'properties': {'project': {'type': 'string'}}, 'additionalProperties': False,
    },
    'lead.resume': {
        'type': 'object', 'properties': {'project': {'type': 'string'}}, 'additionalProperties': False,
    },
    'task.activity': {
        'type': 'object', 'required': ['assignment'],
        'properties': {'assignment': {'type': 'string'}}, 'additionalProperties': False,
    },
    'task.ack': {
        'type': 'object', 'required': ['assignment', 'approved'],
        'properties': {'assignment': {'type': 'string'}, 'note': {'type': 'string'},
                       'approved': {'type': 'boolean'}}, 'additionalProperties': False,
    },
    "contract.show": {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "additionalProperties": False,
    },
    "policy.show": {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "additionalProperties": False,
    },
    "work.create": {
        "type": "object",
        "properties": {
            "project": {"type": "string"},
            "task": {"type": "string"},
            "include_diff": {"type": "boolean"},
            "diff_lines": {"type": "integer"},
        },
        "additionalProperties": False,
    },
    "packet.preflight": {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    },
    "verify.run": {
        "type": "object",
        "required": ["work", "approved"],
        "properties": {
            "work": {"type": "string"},
            "approved": {"type": "boolean"},
            "limit": {"type": "integer"},
            "timeout": {"type": "integer"},
            "output": {"type": "string"},
            "allow_unsafe_output": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "quality.report": {
        "type": "object",
        "required": ["work"],
        "properties": {
            "work": {"type": "string"},
            "verify": {"type": "string"},
            "sidecar": {"type": "string"},
            "decision": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "evidence.append": {
        "type": "object",
        "required": ["work", "verification", "quality_report", "approved"],
        "properties": {
            "work": {"type": "string"},
            "verification": {"type": "string"},
            "quality_report": {"type": "string"},
            "quality_eval": {"type": "string"},
            "ledger_root": {"type": "string", "description": "Defaults to the work plan project's runs/ directory."},
            "sidecar_report": {"type": "string"},
            "decision": {"type": "string"},
            "actor": {"type": "string", "description": "Recorded label; not authenticated."},
            "task_id": {"type": "string", "pattern": evidence.TASK_ID_PATTERN},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "audit.show": {
        "type": "object",
        "required": ["work"],
        "properties": {
            "work": {"type": "string"},
            "ledger_root": {"type": "string", "description": "Defaults to the work plan project's runs/ directory."},
            "task_id": {"type": "string", "pattern": evidence.TASK_ID_PATTERN},
        },
        "additionalProperties": False,
    },
    "run.status": {
        "type": "object",
        "required": ["run"],
        "properties": {"run": {"type": "string"}},
        "additionalProperties": False,
    },
    "run.validate": {
        "type": "object",
        "required": ["run"],
        "properties": {"run": {"type": "string"}},
        "additionalProperties": False,
    },
    "handoff.create": {
        "type": "object",
        "required": ["run", "approved"],
        "properties": {"run": {"type": "string"}, "approved": {"type": "boolean"}},
        "additionalProperties": False,
    },
    "release.claim": {
        "type": "object",
        "required": ["run", "claim", "approved"],
        "properties": {
            "run": {"type": "string"},
            "claim": {"type": "string"},
            "human_decision": {"type": "string"},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.show": {
        "type": "object",
        "required": ["task"],
        "properties": {"task": {"type": "string"}},
        "additionalProperties": False,
    },
    "task.brief": {
        "type": "object",
        "required": ["task", "role"],
        "properties": {
            "task": {"type": "string"},
            "role": {"type": "string", "enum": list(ledger.ROLES)},
            "scope": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    },
    "task.context": {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "additionalProperties": False,
    },
    "task.events": {
        "type": "object",
        "required": ["task"],
        "properties": {"task": {"type": "string"}, "after": {"type": "integer"}},
        "additionalProperties": False,
    },
    "task.list": {
        "type": "object",
        "properties": {
            "project": {"type": "string"},
            "status": {"type": "string", "enum": list(ledger.STATUSES)},
        },
        "additionalProperties": False,
    },
    "task.board": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "task.findings": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "status": {"type": "string", "enum": list(ledger.FINDING_STATUSES)},
            "severity": {"type": "string", "enum": list(ledger.FINDING_SEVERITIES)},
        },
        "additionalProperties": False,
    },
    "task.finding_add": {
        "type": "object",
        "required": ["task", "actor", "severity", "title", "approved"],
        "properties": {
            "task": {"type": "string"},
            "actor": {"type": "string"},
            "severity": {"type": "string", "enum": list(ledger.FINDING_SEVERITIES)},
            "title": {"type": "string"},
            "detail": {"type": "string"},
            "location": {"type": "string"},
            "source_assignment": {"type": "string"},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.finding_update": {
        "type": "object",
        "required": ["finding", "status", "actor", "approved"],
        "properties": {
            "finding": {"type": "string"},
            "status": {"type": "string", "enum": list(ledger.FINDING_STATUSES)},
            "actor": {"type": "string"},
            "resolution_note": {"type": "string"},
            "resolved_by_assignment": {
                "type": "string", "description": "Fixing assignment, including another task's assignment; the resolution event records the fix task.",
            },
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.open": {
        "type": "object",
        "required": ["project", "title", "objective", "approved"],
        "properties": {
            "project": {"type": "string"},
            "title": {"type": "string"},
            "objective": {"type": "string"},
            "acceptance": {"type": "array", "items": {"type": "string"}},
            "lead": {"type": "string"},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.assign": {
        "type": "object",
        "required": ["task", "actor", "role", "mode", "approved"],
        "properties": {
            "task": {"type": "string"},
            "actor": {"type": "string"},
            "role": {"type": "string", "enum": list(ledger.ROLES)},
            "mode": {"type": "string", "enum": list(ledger.MODES)},
            "scope": {"type": "array", "items": {"type": "string"}},
            "owner_pid": {"type": "integer", "minimum": 1, "maximum": 2147483647},
            "owner_boot_id": {"type": "string", "minLength": 1},
            "owner_host": {"type": "string", "minLength": 1},
            "owner_pid_namespace": {"type": "string", "minLength": 1},
            "lease_seconds": {"type": "integer", "minimum": 0},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.finish": {
        "type": "object",
        "required": ["assignment", "result", "summary", "approved"],
        "properties": {
            "assignment": {"type": "string"},
            "result": {"anyOf": [
                {"type": "string", "enum": list(ledger.RESULTS)},
                ledger.TASK_RESULT_SCHEMA,
            ]},
            "summary": {"type": "string"},
            "ref": {"type": "array", "items": {"type": "string"}},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.reap": {
        "type": "object",
        "required": ["approved"],
        "properties": {
            "task": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.event": {
        "type": "object",
        "required": ["task", "actor", "type", "summary", "approved"],
        "properties": {
            "task": {"type": "string"},
            "actor": {"type": "string"},
            "type": {"type": "string"},
            "summary": {"type": "string"},
            "ref": {"type": "array", "items": {"type": "string"}},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "task.status": {
        "type": "object",
        "required": ["task", "to", "actor", "approved"],
        "properties": {
            "task": {"type": "string"},
            "to": {"type": "string", "enum": list(ledger.STATUSES)},
            "actor": {"type": "string"},
            "summary": {"type": "string"},
            "force": {"type": "boolean"},
            "reason": {"type": "string"},
            "approved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}


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
        "approval_required": name in MCP_GATED_TOOLS,
        "description": _description(name),
        "fallback_command": _fallback_command(name),
        "input_schema": MCP_INPUT_SCHEMAS[name],
    }


def _description(name: str) -> str:
    descriptions = {
        'task.now': 'Read lead attention in priority order, pending results, activity, findings and cleanup.',
        'lead.resume': 'Restore deterministic lead context, decisions, verification freshness and project git state (read-only).',
        "contract.show": "Read the local Hipson agent contract.",
        "policy.show": "Read the merged project policy.",
        "work.create": "Create a provider-free work plan and optional packet path.",
        "packet.preflight": "Validate a bounded packet before any provider-backed sidecar.",
        "verify.run": "Run selected local verification commands and record bounded output.",
        "quality.report": "Correlate work, verification, and optional sidecar artifacts.",
        "evidence.append": "Append a local evidence record to the run ledger.",
        "audit.show": "Read the audit bundle for handoff and release gate status.",
        "run.status": "Read the status of a Hipson run bundle.",
        "run.validate": "Validate expected run bundle artifacts and artifact kinds.",
        "handoff.create": "Write a compact agent handoff artifact for the run bundle.",
        "release.claim": "Evaluate and record a release claim against run evidence.",
        "task.show": "Read a central ledger task with all assignments and current claims.",
        "task.brief": "Read a bounded worker brief for a task, role and optional scope.",
        "task.context": "Read worker context; defaults to HIPSON_TASK and marks HIPSON_ASSIGNMENT as you.",
        "task.events": "Read task events after an exclusive sequence cursor (default 0).",
        "task.list": "List central ledger tasks, optionally filtered by project and status.",
        "task.board": "Read active tasks and unacknowledged assignment results across all projects.",
        "task.findings": "Read findings, optionally filtered by task, status and severity.",
        "task.finding_add": "Open a finding and append its task event on behalf of the lead.",
        "task.finding_update": "Resolve or reopen a finding and append its task event.",
        "task.open": "Open a lead-managed task in the central ledger (default lead: claude).",
        "task.assign": "Record a worker assignment and claim rw scope on behalf of the lead.",
        "task.finish": "Finish an assignment and release its path claims.",
        'task.activity': 'Read bounded, redacted assignment activity and failure journal as untrusted evidence.',
        'task.ack': 'Explicitly acknowledge a delivered assignment result or failure journal on behalf of the lead.',
        "task.reap": "Cancel orphaned assignments and release claims, or preview with dry_run.",
        "task.event": "Append a lead-recorded task event on behalf of a worker.",
        "task.status": "Transition a task's lifecycle status on behalf of the lead.",
    }
    return descriptions[name]


def _fallback_command(name: str) -> str:
    commands = {
        'task.now': 'hipson task now --json',
        'lead.resume': 'hipson lead resume --json',
        "contract.show": "hipson contract show --json",
        "policy.show": "hipson policy show --json",
        "work.create": "hipson work --task \"...\" --write-packet --work-output runs/work.json --json",
        "packet.preflight": "hipson packet preflight runs/review-packet.md -o runs/preflight.json --json",
        "verify.run": "hipson verify run --work runs/work.json -o runs/verify.json --json",
        "quality.report": "hipson quality report --work runs/work.json --verify runs/verify.json -o runs/quality.json --json",
        "evidence.append": "hipson evidence append --work runs/work.json --verification runs/verify.json --quality-report runs/quality.json --json",
        "audit.show": "hipson audit show --work runs/work.json --json",
        "run.status": "hipson run status --run runs/<work_id> --json",
        "run.validate": "hipson run validate --run runs/<work_id> --json",
        "handoff.create": "hipson run handoff --run runs/<work_id> --json",
        "release.claim": "hipson release claim --run runs/<work_id> --claim \"release readiness\" --human-decision approved --json",
        "task.show": "hipson task show --task T-0001 --json",
        "task.brief": "hipson task brief --task T-0001 --role builder --scope src --json",
        "task.context": "hipson task context --json",
        "task.events": "hipson task events --task T-0001 --after 0 --json",
        "task.list": "hipson task list --json",
        "task.board": "hipson task board --json",
        "task.findings": "hipson task finding list --json",
        "task.finding_add": 'hipson task finding add --task T-0001 --actor claude --severity major --title "..." --json',
        "task.finding_update": "hipson task finding update --finding F-0001 --status fixed --actor claude --json",
        "task.open": "hipson task open --project . --title \"...\" --objective \"...\" --json",
        "task.assign": "hipson task assign --task T-0001 --actor codex --role builder --mode rw --scope src --json",
        "task.finish": "hipson task finish --assignment A-0001 --result passed --summary \"...\" --json",
        'task.activity': 'hipson task activity --assignment A-0001 --json',
        'task.ack': 'hipson task ack --assignment A-0001 --json',
        "task.reap": "hipson task reap --dry-run --json",
        "task.event": "hipson task event --task T-0001 --actor codex --type progress --summary \"...\" --json",
        "task.status": "hipson task status --task T-0001 --to review --actor claude --json",
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
    except (OSError, json.JSONDecodeError, ValueError, RuntimeError, sqlite3.Error) as exc:
        return _tool_error(request_id, str(exc))
    return _error(request_id, -32601, f"Unknown method: {method}")


def _mcp_tool_descriptor(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": _description(name),
        "inputSchema": MCP_INPUT_SCHEMAS[name],
    }


def _read_resource(params: object, *, project: Path) -> dict[str, Any]:
    uri = str(params.get("uri", "")) if isinstance(params, dict) else ""
    if uri == "hipson://contract":
        payload = agent_contract.build_agent_contract(project)
    elif uri == "hipson://policy":
        payload = policy.load_policy(project)
    elif uri == "hipson://latest-audit":
        payload = evidence.latest_audit_bundle(project)
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


def _tool_payload(name: str, arguments: dict[str, Any], *, project: Path) -> object:
    if (name.startswith("task.") or name == 'lead.resume') and name in MCP_INPUT_SCHEMAS:
        return _task_tool_payload(name, arguments)
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
        work_plan = verification.load_work_plan(str(arguments.get("work", "")))
        return evidence.audit_bundle(
            work_path=str(arguments.get("work", "")),
            ledger_root=evidence.evidence_dir(arguments.get("ledger_root"), project=work_plan.get("project")),
            task_id=arguments.get("task_id"),
        )
    if name == "run.status":
        return run_control.build_status(str(arguments.get("run", "")))
    if name == "run.validate":
        return run_control.build_validation(str(arguments.get("run", "")))
    if name == "handoff.create":
        _require_mcp_approval(arguments, name)
        run_dir = run_control.resolve_run(str(arguments.get("run", "")))
        handoff = run_control.build_handoff(run_dir)
        outputs = run_control.write_handoff(run_dir, handoff)
        return {**handoff, "outputs": outputs}
    if name == "release.claim":
        _require_mcp_approval(arguments, name)
        run_dir = run_control.resolve_run(str(arguments.get("run", "")))
        claim = run_control.build_release_claim(
            run_dir,
            claim=str(arguments.get("claim", "")),
            human_decision=str(arguments.get("human_decision", "pending")),
        )
        output = run_control.write_release_claim(run_dir, claim)
        return {**claim, "output": str(output)}
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
            actor=arguments.get("actor"),
            task_id=arguments.get("task_id"),
        )
        path = evidence.append_record(ledger_root, record)
        return {"ledger": str(path), "record": record}
    raise SystemExit(f"Unknown tool: {name}")


def _task_tool_payload(name: str, arguments: dict[str, Any]) -> object:
    if name in MCP_GATED_TOOLS:
        _require_mcp_approval(arguments, name)
    _validate_task_arguments(name, arguments)
    if name == "task.context":
        arguments = {**arguments, "task": task_context.context_task(arguments.get("task"))}
    if "project" in arguments:
        arguments = {**arguments, "project": str(Path(arguments["project"]).expanduser().resolve())}
    # The ledger is central: never derive its DB or project filters from --project.
    db = ledger.default_ledger_db()
    dry_run = name == "task.reap" and arguments.get("dry_run", False)
    create = name in MCP_GATED_TOOLS and not dry_run
    if not create and not db.exists():
        if name in ('task.now', 'lead.resume'):
            return lead_context.build(None, project=arguments.get('project'), resume=name == 'lead.resume')
        if name == 'task.activity':
            raise ledger.LedgerError(f"Assignment not found: {arguments['assignment']}")
        if name in ("task.show", "task.events", "task.brief", "task.context") or (name in ("task.reap", "task.findings") and "task" in arguments):
            raise ledger.LedgerError(f"Task not found: {arguments['task']}")
        return []
    # closing() preserves the ledger's own transactions, including committed conflicts.
    with closing(ledger.Ledger(db, create=create, read_only=not create)) as store:
        if name in ('task.now', 'lead.resume'):
            return lead_context.build(store, project=arguments.get('project'), resume=name == 'lead.resume')
        if name == 'task.activity':
            return store.activity(arguments['assignment'])
        if name == 'task.ack':
            return store.ack(arguments['assignment'], note=arguments.get('note', ''))
        if name == "task.open":
            return store.open_task(
                project=arguments["project"], title=arguments["title"], objective=arguments["objective"],
                acceptance=arguments.get("acceptance", []), lead=arguments.get("lead", "claude"),
            )
        if name == "task.assign":
            return store.assign(
                arguments["task"], actor=arguments["actor"], role=arguments["role"], mode=arguments["mode"],
                scope=arguments.get("scope", []),
                owner_pid=arguments.get("owner_pid"), owner_boot_id=arguments.get("owner_boot_id"),
                owner_host=arguments.get("owner_host"), lease_seconds=arguments.get("lease_seconds"),
                owner_pid_namespace=arguments.get("owner_pid_namespace"),
            )
        if name == "task.finish":
            result = arguments["result"]
            report = ledger.validate_task_result(result) if isinstance(result, dict) else None
            outcome = ("passed" if report["status"] == "completed" else "failed") if report is not None else result
            return store.finish(
                arguments["assignment"], result=outcome, summary=arguments["summary"],
                refs=arguments.get("ref", []), worker_result=report,
            )
        if name == "task.finding_add":
            return store.add_finding(
                arguments["task"], actor=arguments["actor"], severity=arguments["severity"], title=arguments["title"],
                detail=arguments.get("detail", ""), location=arguments.get("location", ""),
                source_assignment=arguments.get("source_assignment"),
            )
        if name == "task.finding_update":
            return store.update_finding(
                arguments["finding"], actor=arguments["actor"], status=arguments["status"],
                resolution_note=arguments.get("resolution_note", ""),
                resolved_by_assignment=arguments.get("resolved_by_assignment"),
            )
        if name == "task.findings":
            return store.findings(
                task=arguments.get("task"), status=arguments.get("status"), severity=arguments.get("severity"),
            )
        if name == "task.reap":
            return store.reap(task=arguments.get("task"), dry_run=dry_run)
        if name == "task.event":
            return store.add_event(
                arguments["task"], actor=arguments["actor"], type=arguments["type"], summary=arguments["summary"],
                refs=arguments.get("ref", []),
            )
        if name == "task.status":
            return store.set_status(
                arguments["task"], to=arguments["to"], actor=arguments["actor"], summary=arguments.get("summary", ""),
                force=arguments.get("force", False), reason=arguments.get("reason", ""),
            )
        if name == "task.show":
            return store.show(arguments["task"])
        if name == "task.brief":
            return task_context.brief(store, arguments["task"], role=arguments["role"], scope=arguments.get("scope", []))
        if name == "task.context":
            return task_context.context(store, arguments["task"])
        if name == "task.events":
            return store.events(arguments["task"], after=arguments.get("after", 0))
        if name == "task.list":
            return store.list_tasks(project=arguments.get("project"), status=arguments.get("status"))
        return store.board()


def _validate_task_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Enforce task schemas and store-independent semantics before opening a database."""
    schema = MCP_INPUT_SCHEMAS[name]
    for key in schema.get("required", []):
        if key not in arguments:
            raise ValueError(f"{name} requires argument: {key}")
    types = {"string": str, "integer": int, "boolean": bool, "array": list}
    for key, value in arguments.items():
        field = schema["properties"].get(key)
        if field is None:
            raise ValueError(f"{name}: unknown argument: {key}")
        if name == "task.finish" and key == "result":
            if isinstance(value, dict):
                ledger.validate_task_result(value)
            elif not isinstance(value, str) or value not in ledger.RESULTS:
                raise ValueError(f"{name}: result must be one of {ledger.RESULTS} or a worker result object")
            continue
        if type(value) is not types[field["type"]]:
            raise ValueError(f"{name}: {key} must be {field['type']}")
        if isinstance(value, list) and any(not isinstance(item, str) for item in value):
            raise ValueError(f"{name}: {key} must contain strings")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"{name}: {key} must be one of {field['enum']}")
        if "minimum" in field and value < field["minimum"]:
            raise ValueError(f"{name}: {key} must be at least {field['minimum']}")
        if "maximum" in field and value > field["maximum"]:
            raise ValueError(f"{name}: {key} must be at most {field['maximum']}")
        if isinstance(value, str) and len(value) < field.get("minLength", 0):
            raise ValueError(f"{name}: {key} must not be empty")
    text_fields = {
        "task.open": ("title", "objective", "lead"),
        "task.assign": ("actor", "owner_boot_id", "owner_host", "owner_pid_namespace"),
        "task.finish": ("summary",),
        "task.event": ("actor", "type", "summary"),
        "task.status": ("actor",),
        "task.finding_add": ("actor", "title"),
        "task.finding_update": ("actor",),
    }
    for key in text_fields.get(name, ()):
        if key in arguments:
            ledger.require_text(arguments[key], key)
    if name == "task.assign":
        ledger.validate_assignment_scope(arguments.get("scope", []), mode=arguments["mode"])
    if name == "task.status":
        ledger.validate_status_override(to=arguments["to"], force=arguments.get("force", False),
                                        reason=arguments.get("reason", ""))
    if name == "task.brief":
        ledger.normalize_scope(arguments.get("scope", []))
    if name == "task.finding_update" and arguments["status"] == "open":
        ledger.require_text(arguments.get("resolution_note", ""), "Reopening note")
        if "resolved_by_assignment" in arguments:
            raise ValueError("A reopened finding cannot have a resolved-by assignment")


def _require_mcp_approval(arguments: dict[str, Any], tool_name: str) -> None:
    if arguments.get("approved") is not True:
        raise SystemExit(f"{tool_name} requires approved=true for MCP write/gated execution")


def _tool_error(request_id: object, message: str) -> dict[str, Any]:
    return _result(request_id, {"content": [{"type": "text", "text": redact_text(message)}], "isError": True})


def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
