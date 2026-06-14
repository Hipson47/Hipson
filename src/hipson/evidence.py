"""Local evidence ledger and audit bundle helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hipson.contracts import SCHEMA_VERSION, new_id, stable_hash, timestamp, work_run_from_plan
from hipson.redaction import redact_text
from hipson.verification import load_work_plan

LEDGER_FILE = "evidence.jsonl"


def evidence_dir(path: str | None = None, *, project: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    if project:
        return Path(project).expanduser().resolve() / "runs"
    return Path.cwd().resolve() / "runs"


def ledger_path(root: Path) -> Path:
    return root / LEDGER_FILE


def read_records(root: Path) -> list[dict[str, Any]]:
    path = ledger_path(root)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def latest_hash(root: Path) -> str:
    records = read_records(root)
    return str(records[-1].get("record_hash", "")) if records else ""


def append_record(root: Path, record: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = ledger_path(root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def build_evidence_record(
    *,
    work_plan: dict[str, Any],
    verification: dict[str, Any] | None = None,
    quality_report: dict[str, Any] | None = None,
    quality_eval: dict[str, Any] | None = None,
    sidecar_report: str | None = None,
    ledger_root: Path,
    human_decision: str = "pending",
) -> dict[str, Any]:
    verification_payload = verification or {}
    quality_report_payload = quality_report or {}
    quality_eval_payload = quality_eval or {}
    provider_used = bool(sidecar_report or _ai_quality_enabled(work_plan))
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id("evidence"),
        "previous_hash": latest_hash(ledger_root),
        "created_at_utc": timestamp(),
        "work": work_run_from_plan(work_plan),
        "provider": {
            "used": provider_used,
            "sidecar_report": redact_text(sidecar_report or ""),
        },
        "verification": verification_payload,
        "quality": {
            "report": quality_report_payload,
            "eval": quality_eval_payload,
            "summary": _quality_summary(quality_report_payload, quality_eval_payload),
        },
        "claims": {
            "safe": _safe_claims(work_plan, verification_payload, quality_report_payload, quality_eval_payload),
            "unsafe": _unsafe_claims(work_plan, verification_payload, quality_report_payload, quality_eval_payload),
            "evidence_refs": [],
        },
        "unknowns": _unknowns(work_plan, verification_payload, quality_report_payload, quality_eval_payload),
        "human_decision": {"required": True, "outcome": redact_text(human_decision)},
    }
    record["record_hash"] = stable_hash({key: value for key, value in record.items() if key != "record_hash"})
    return record


def load_json_artifact(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    artifact = Path(path).expanduser().resolve()
    try:
        data = json.loads(artifact.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Artifact does not exist: {artifact}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON artifact: {artifact}: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"JSON artifact must be an object: {artifact}")
    return data


def audit_bundle(*, work_path: str, ledger_root: Path) -> dict[str, Any]:
    work_plan = load_work_plan(work_path)
    work_id = str(work_plan.get("work_id", ""))
    records = [
        record
        for record in read_records(ledger_root)
        if str(record.get("work", {}).get("work_id", "")) == work_id
    ]
    latest = records[-1] if records else None
    latest_quality = latest.get("quality", {}) if latest else {}
    latest_quality_summary = latest_quality.get("summary", {}) if isinstance(latest_quality, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "work": work_run_from_plan(work_plan),
        "evidence_records": records,
        "latest_status": latest.get("verification", {}).get("status", "no-evidence") if latest else "no-evidence",
        "latest_quality_gate": latest_quality_summary.get("quality_gate", "no-quality") if latest else "no-evidence",
        "latest_eval_ok": latest_quality_summary.get("eval_ok", "no-eval") if latest else "no-evidence",
        "unknowns": latest.get("unknowns", []) if latest else ["No evidence record found for this work plan."],
    }


def _ai_quality_enabled(work_plan: dict[str, Any]) -> bool:
    quality = work_plan.get("ai_quality", {})
    return isinstance(quality, dict) and bool(quality.get("enabled"))


def _safe_claims(
    work_plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
) -> list[str]:
    claims = ["work plan generated locally"]
    if not _ai_quality_enabled(work_plan):
        claims.append("provider-free work planning")
    if verification.get("status") == "passed":
        claims.append("listed verification commands passed")
    if quality_report.get("quality_gate") == "passed":
        claims.append("quality report gate passed")
    if quality_eval.get("ok") is True:
        claims.append("quality eval passed")
    return claims


def _unsafe_claims(
    work_plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
) -> list[str]:
    claims = ["sidecar output correctness", "production release readiness"]
    if verification.get("status") != "passed":
        claims.append("verification passed")
    if quality_report and quality_report.get("quality_gate") != "passed":
        claims.append("quality report gate passed")
    if quality_eval and quality_eval.get("ok") is not True:
        claims.append("quality eval passed")
    if _ai_quality_enabled(work_plan):
        claims.append("AI quality pass approved the work")
    return claims


def _unknowns(
    work_plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
) -> list[str]:
    unknowns: list[str] = []
    if not verification:
        unknowns.append("Verification commands have not been recorded.")
    elif verification.get("status") != "passed":
        unknowns.append("At least one verification command failed or was not executable.")
    if _ai_quality_enabled(work_plan):
        unknowns.append("AI sidecar output remains advisory until checked by a human.")
        if not quality_report:
            unknowns.append("Quality report has not been recorded.")
        if not quality_eval:
            unknowns.append("Quality eval has not been recorded.")
    if quality_report and quality_report.get("quality_gate") != "passed":
        unknowns.append("Quality report gate is not passed.")
    if quality_eval and quality_eval.get("ok") is not True:
        unknowns.append("Quality eval reported issues.")
    return unknowns


def _quality_summary(quality_report: dict[str, Any], quality_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_gate": str(quality_report.get("quality_gate", "missing")) if quality_report else "missing",
        "verification_status": str(quality_report.get("verification_status", "")) if quality_report else "",
        "sidecar_present": bool(quality_report.get("sidecar_present", False)) if quality_report else False,
        "finding_count": int(quality_report.get("sidecar", {}).get("finding_count", 0) or 0)
        if isinstance(quality_report.get("sidecar"), dict)
        else 0,
        "eval_ok": bool(quality_eval.get("ok")) if quality_eval else False,
        "eval_score": int(quality_eval.get("score", 0) or 0) if quality_eval else 0,
        "eval_issue_count": len(quality_eval.get("issues", [])) if isinstance(quality_eval.get("issues"), list) else 0,
    }
