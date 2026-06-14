"""Run bundle status, validation, handoff, and release claim helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson.contracts import SCHEMA_VERSION, sha256_text, timestamp
from hipson.evidence import LEDGER_FILE
from hipson.redaction import redact_text

RUN_ARTIFACTS = {
    "contract": "contract.json",
    "work": "work.json",
    "packet": "review-packet.md",
    "preflight": "preflight.json",
    "verification": "verify.json",
    "quality": "quality.json",
    "quality_eval": "quality-eval.json",
    "evidence": LEDGER_FILE,
    "audit": "audit.json",
    "summary": "summary.md",
    "manifest": "manifest.json",
    "handoff": "handoff.md",
    "handoff_json": "handoff.json",
    "release_claim": "release-claim.json",
}

JSON_ARTIFACT_KINDS = {
    "contract": "hipson.agent_contract",
    "work": "hipson.work_plan",
    "preflight": "hipson.packet_preflight",
    "verification": "hipson.verification",
    "quality": "hipson.quality_report",
    "quality_eval": "hipson.quality_eval",
    "audit": "hipson.audit_bundle",
    "manifest": "hipson.run_manifest",
    "handoff_json": "hipson.agent_handoff",
    "release_claim": "hipson.release_claim",
}

REQUIRED_RUN_ARTIFACTS = (
    "contract",
    "work",
    "packet",
    "preflight",
    "verification",
    "quality",
    "evidence",
    "audit",
    "summary",
)


def resolve_run(run_path: str | Path) -> Path:
    run_dir = Path(run_path).expanduser()
    if not run_dir.is_absolute():
        run_dir = Path.cwd() / run_dir
    run_dir = run_dir.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise SystemExit(f"Run directory does not exist: {run_dir}")
    return run_dir


def paths(run_dir: Path) -> dict[str, Path]:
    return {name: run_dir / file_name for name, file_name in RUN_ARTIFACTS.items()}


def build_manifest(
    run_dir: Path,
    *,
    workflow: str,
    mode: str,
    verify_profile: str,
    status: str,
    sidecar: dict[str, Any],
    gates: dict[str, Any],
    created_artifacts: list[str] | None = None,
    updated_artifacts: list[str] | None = None,
    rerun_steps: list[str] | None = None,
) -> dict[str, Any]:
    work = _load_optional_json(run_dir / RUN_ARTIFACTS["work"])
    return {
        "artifact_kind": "hipson.run_manifest",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": timestamp(),
        "work_id": str(work.get("work_id", "")) if work else run_dir.name,
        "run_dir": str(run_dir),
        "workflow": workflow,
        "mode": mode,
        "verify_profile": verify_profile,
        "status": status,
        "sidecar": sidecar,
        "gates": gates,
        "created_artifacts": created_artifacts or [],
        "updated_artifacts": updated_artifacts or [],
        "rerun_steps": rerun_steps or [],
        "artifacts": artifact_snapshot(run_dir),
    }


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    path = run_dir / RUN_ARTIFACTS["manifest"]
    _write_json(path, manifest)
    return path


def build_status(run_path: str | Path) -> dict[str, Any]:
    run_dir = resolve_run(run_path)
    work = _load_optional_json(run_dir / RUN_ARTIFACTS["work"])
    audit = _load_optional_json(run_dir / RUN_ARTIFACTS["audit"])
    manifest = _load_optional_json(run_dir / RUN_ARTIFACTS["manifest"])
    gates = audit.get("latest_gates", {}) if isinstance(audit, dict) else {}
    validation = build_validation(run_dir)
    return {
        "artifact_kind": "hipson.run_status",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": timestamp(),
        "work_id": str(work.get("work_id", "")) if work else run_dir.name,
        "run_dir": str(run_dir),
        "status": _run_status(audit, validation),
        "workflow": str(manifest.get("workflow", "")) if manifest else "",
        "mode": str(manifest.get("mode", "")) if manifest else "",
        "gates": gates if isinstance(gates, dict) else {},
        "latest_status": str(audit.get("latest_status", "no-audit")) if audit else "no-audit",
        "release_claim_gate": str(audit.get("latest_release_claim_gate", "no-audit")) if audit else "no-audit",
        "missing_required": validation["missing_required"],
        "artifacts": artifact_snapshot(run_dir),
        "recommended_next_command": _recommended_next_command(run_dir, gates if isinstance(gates, dict) else {}),
    }


def build_validation(run_path: str | Path) -> dict[str, Any]:
    run_dir = resolve_run(run_path)
    path_map = paths(run_dir)
    missing = [name for name in REQUIRED_RUN_ARTIFACTS if not path_map[name].exists()]
    invalid_json: list[str] = []
    kind_mismatches: list[str] = []
    for name, expected_kind in JSON_ARTIFACT_KINDS.items():
        artifact_path = path_map[name]
        if not artifact_path.exists():
            continue
        try:
            payload = _load_json(artifact_path)
        except SystemExit as exc:
            invalid_json.append(f"{name}: {exc}")
            continue
        actual_kind = payload.get("artifact_kind")
        if actual_kind != expected_kind:
            kind_mismatches.append(f"{name}: expected {expected_kind}, got {actual_kind}")
    issues = [f"Missing required artifact: {name}" for name in missing]
    issues.extend(f"Invalid JSON artifact: {item}" for item in invalid_json)
    issues.extend(f"Artifact kind mismatch: {item}" for item in kind_mismatches)
    return {
        "artifact_kind": "hipson.run_validation",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": timestamp(),
        "run_dir": str(run_dir),
        "ok": not issues,
        "missing_required": missing,
        "invalid_json": invalid_json,
        "kind_mismatches": kind_mismatches,
        "issues": issues,
        "artifacts": artifact_snapshot(run_dir),
    }


def build_handoff(run_path: str | Path) -> dict[str, Any]:
    run_dir = resolve_run(run_path)
    work = _load_optional_json(run_dir / RUN_ARTIFACTS["work"])
    audit = _load_optional_json(run_dir / RUN_ARTIFACTS["audit"])
    summary_path = run_dir / RUN_ARTIFACTS["summary"]
    summary_text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    latest_record = _latest_evidence_record(run_dir)
    gates = audit.get("latest_gates", {}) if isinstance(audit, dict) else {}
    claims = latest_record.get("claims", {}) if isinstance(latest_record, dict) else {}
    unknowns = latest_record.get("unknowns", []) if isinstance(latest_record, dict) else []
    return {
        "artifact_kind": "hipson.agent_handoff",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": timestamp(),
        "work_id": str(work.get("work_id", "")) if work else run_dir.name,
        "run_dir": str(run_dir),
        "task": redact_text(str(work.get("task", ""))) if work else "",
        "project": str(work.get("project", "")) if work else "",
        "status": _run_status(audit, build_validation(run_dir)),
        "gates": gates if isinstance(gates, dict) else {},
        "safe_claims": claims.get("safe", []) if isinstance(claims, dict) else [],
        "unsafe_claims": claims.get("unsafe", []) if isinstance(claims, dict) else [],
        "unknowns": unknowns if isinstance(unknowns, list) else [],
        "summary_sha256": sha256_text(summary_text),
        "next_agent_step": _next_agent_step(gates if isinstance(gates, dict) else {}, unknowns),
        "artifacts": artifact_snapshot(run_dir),
    }


def write_handoff(run_dir: Path, handoff: dict[str, Any]) -> dict[str, str]:
    json_path = run_dir / RUN_ARTIFACTS["handoff_json"]
    markdown_path = run_dir / RUN_ARTIFACTS["handoff"]
    _write_json(json_path, handoff)
    markdown_path.write_text(render_handoff(handoff), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def render_handoff(handoff: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Hipson Agent Handoff",
            "",
            f"- Work ID: `{handoff.get('work_id', '')}`",
            f"- Status: `{handoff.get('status', '')}`",
            f"- Project: `{handoff.get('project', '')}`",
            f"- Task: {handoff.get('task', '')}",
            "",
            "## Gates",
            *_lines_from_dict(handoff.get("gates", {}), empty="No gates recorded."),
            "",
            "## Safe Claims",
            *_lines_from_list(handoff.get("safe_claims", []), empty="No safe claims recorded."),
            "",
            "## Unknowns",
            *_lines_from_list(handoff.get("unknowns", []), empty="No unknowns recorded."),
            "",
            "## Next Agent Step",
            f"- {handoff.get('next_agent_step', '')}",
            "",
        ]
    ).rstrip() + "\n"


def build_release_claim(run_path: str | Path, *, claim: str, human_decision: str = "pending") -> dict[str, Any]:
    run_dir = resolve_run(run_path)
    status = build_status(run_dir)
    audit = _load_optional_json(run_dir / RUN_ARTIFACTS["audit"])
    gates = audit.get("latest_gates", {}) if isinstance(audit, dict) else {}
    human_gate = _human_decision_gate(human_decision)
    release_gate = str(gates.get("release_claim_gate", "no-evidence")) if isinstance(gates, dict) else "no-evidence"
    verification_gate = str(gates.get("verification_gate", "no-evidence")) if isinstance(gates, dict) else "no-evidence"
    allowed = release_gate == "passed" and verification_gate == "passed" and human_gate == "passed"
    reasons: list[str] = []
    if verification_gate != "passed":
        reasons.append("verification_gate is not passed")
    if release_gate != "passed":
        reasons.append("release_claim_gate is not passed in audit evidence")
    if human_gate != "passed":
        reasons.append("human decision for this claim is not approved")
    return {
        "artifact_kind": "hipson.release_claim",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": timestamp(),
        "work_id": str(status.get("work_id", "")),
        "run_dir": str(run_dir),
        "claim": redact_text(claim),
        "allowed": allowed,
        "status": "allowed" if allowed else "blocked",
        "human_decision": redact_text(human_decision),
        "gates": gates if isinstance(gates, dict) else {},
        "reasons": reasons,
        "safe_to_claim": [claim] if allowed else [],
        "unsafe_to_claim": [] if allowed else [claim],
        "audit": str(run_dir / RUN_ARTIFACTS["audit"]),
    }


def write_release_claim(run_dir: Path, claim: dict[str, Any]) -> Path:
    path = run_dir / RUN_ARTIFACTS["release_claim"]
    _write_json(path, claim)
    return path


def artifact_snapshot(run_dir: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for name, file_name in RUN_ARTIFACTS.items():
        path = run_dir / file_name
        snapshot[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
        }
    return snapshot


def _run_status(audit: dict[str, Any] | None, validation: dict[str, Any]) -> str:
    if not validation.get("ok"):
        return "incomplete"
    if not audit:
        return "no-audit"
    latest_status = str(audit.get("latest_status", "no-evidence"))
    if latest_status == "passed":
        return "passed"
    if latest_status == "failed":
        return "blocked"
    return latest_status


def _recommended_next_command(run_dir: Path, gates: dict[str, Any]) -> str:
    if gates.get("release_claim_gate") == "passed":
        return f"hipson release claim --run {run_dir} --claim \"release readiness\" --human-decision approved --json"
    if gates.get("verification_gate") != "passed":
        return f"hipson autopilot resume --run {run_dir} --rerun-step verify --json"
    return f"hipson run handoff --run {run_dir} --json"


def _next_agent_step(gates: dict[str, Any], unknowns: object) -> str:
    values = unknowns if isinstance(unknowns, list) else []
    if values:
        return f"Resolve the first unknown: {values[0]}"
    if gates.get("release_claim_gate") == "passed":
        return "Ask for a final human release decision, then record a release claim if approved."
    return "Rerun verification or append evidence until blocked gates are resolved."


def _latest_evidence_record(run_dir: Path) -> dict[str, Any]:
    path = run_dir / LEDGER_FILE
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return records[-1] if records else {}


def _load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _load_json(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: {exc}") from None
    if not isinstance(payload, dict):
        raise SystemExit(f"{path}: JSON artifact must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _human_decision_gate(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized in {"accepted", "approved", "pass", "passed", "release", "released", "merge", "merged"}:
        return "passed"
    if normalized in {"rejected", "blocked", "failed", "fail"}:
        return "blocked"
    return "pending"


def _lines_from_dict(value: object, *, empty: str) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"- {empty}"]
    return [f"- `{key}`: `{item}`" for key, item in sorted(value.items())]


def _lines_from_list(value: object, *, empty: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"- {empty}"]
    return [f"- {item}" for item in value]
