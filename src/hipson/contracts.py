"""Versioned local contracts for AI-dev-first Hipson work artifacts."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import TypedDict, cast

from hipson import project as project_mod
from hipson.redaction import redact_text, sanitize_path

SCHEMA_VERSION = "1.0"
ARTIFACT_SCHEMAS = {
    "hipson.agent_bootstrap": "schemas/agent-bootstrap.schema.json",
    "hipson.agent_contract": "schemas/agent-contract.schema.json",
    "hipson.agent_install": "schemas/agent-install.schema.json",
    "hipson.agent_surfaces_doctor": "schemas/agent-surfaces-doctor.schema.json",
    "hipson.audit_bundle": "schemas/audit-bundle.schema.json",
    "hipson.autopilot_implement_run": "schemas/autopilot-implement-run.schema.json",
    "hipson.autopilot_review_run": "schemas/autopilot-review-run.schema.json",
    "hipson.evidence_record": "schemas/evidence-record.schema.json",
    "hipson.mcp_server_catalog": "schemas/mcp-server-catalog.schema.json",
    "hipson.packet_preflight": "schemas/packet-preflight.schema.json",
    "hipson.project_policy": "schemas/project-policy.schema.json",
    "hipson.quality_eval": "schemas/quality-eval.schema.json",
    "hipson.quality_report": "schemas/quality-report.schema.json",
    "hipson.review_kit_run": "schemas/review-kit-run.schema.json",
    "hipson.verification": "schemas/verification.schema.json",
    "hipson.work_plan": "schemas/work-plan.schema.json",
}


class RepoState(TypedDict):
    path: str
    git_root: str
    head: str
    branch: str
    dirty: bool
    diff_hash: str


class PacketManifest(TypedDict):
    mode: str
    path: str
    sha256: str
    written: bool
    redacted: bool
    size_bytes: int


class VerificationResult(TypedDict):
    command: str
    argv: list[str]
    cwd: str
    exit_code: int
    status: str
    duration_ms: int
    started_at_utc: str
    ended_at_utc: str
    stdout: str
    stderr: str
    stdout_sha256: str
    stderr_sha256: str


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(value: object) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def repo_state(project: Path) -> RepoState:
    root = project_mod.git_root(project)
    git_base = root or project
    head = _git_output(git_base, "rev-parse", "HEAD")
    branch = _git_output(git_base, "branch", "--show-current")
    status = _git_output(git_base, "status", "--short")
    diff = _git_output(git_base, "diff", "--binary")
    staged = _git_output(git_base, "diff", "--binary", "--cached")
    return {
        "path": str(project.resolve()),
        "git_root": str(root.resolve()) if root else "",
        "head": head or "unknown",
        "branch": branch or "unknown",
        "dirty": bool(status.strip()),
        "diff_hash": sha256_text(f"{staged}\n{diff}"),
    }


def packet_manifest(packet: dict[str, object]) -> PacketManifest:
    path_text = str(packet.get("path", ""))
    path = Path(path_text).expanduser()
    exists = bool(path_text) and path.exists() and path.is_file()
    content = path.read_bytes() if exists else b""
    return {
        "mode": redact_text(str(packet.get("mode", ""))),
        "path": sanitize_path(path_text),
        "sha256": sha256_bytes(content) if exists else "",
        "written": bool(packet.get("written", False)),
        "redacted": True,
        "size_bytes": len(content),
    }


def work_run_from_plan(plan: dict[str, object]) -> dict[str, object]:
    route = cast(dict[str, object], plan.get("route", {}))
    packet = cast(dict[str, object], plan.get("packet", {}))
    preflight = cast(dict[str, object], plan.get("packet_preflight", {}))
    return {
        "schema_version": SCHEMA_VERSION,
        "work_id": str(plan.get("work_id", "")),
        "created_at_utc": str(plan.get("created_at_utc", "")),
        "task": redact_text(str(plan.get("task", ""))),
        "repo": plan.get("repo_state", {}),
        "route": {
            "mode": redact_text(str(route.get("mode", ""))),
            "risk": redact_text(str(route.get("risk", ""))),
            "recommended_skill": redact_text(str(route.get("recommended_skill", ""))),
            "requires_human_review": bool(route.get("requires_human_review", True)),
        },
        "packet": packet_manifest(packet),
        "packet_preflight": {
            "command": redact_text(str(preflight.get("command", ""))),
            "output": sanitize_path(str(preflight.get("output", ""))),
            "required_before_sidecar": bool(preflight.get("required_before_sidecar", False)),
        },
        "ai_quality": plan.get("ai_quality", {}),
        "verification_commands": [redact_text(str(item)) for item in _list(plan.get("verification"))],
        "memory_commands": [redact_text(str(item)) for item in _list(plan.get("memory"))],
        "audit": [redact_text(str(item)) for item in _list(plan.get("audit"))],
        "unknowns": [
            "Verification commands have not been run unless a verification artifact is attached.",
            "Sidecar output is advisory unless confirmed by local evidence and human review.",
        ],
    }


def _git_output(cwd: Path, *args: str) -> str:
    result = project_mod.run(["git", *args], cwd)
    return redact_text(result.stdout.strip()) if result.code == 0 else ""


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []
