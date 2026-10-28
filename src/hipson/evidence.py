"""Local evidence ledger and audit bundle helpers."""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hipson.contracts import SCHEMA_VERSION, new_id, stable_hash, timestamp, work_run_from_plan
from hipson.redaction import redact_text, redact_value
from hipson.verification import load_work_plan

LEDGER_FILE = "evidence.jsonl"
# Absolute end-of-string in Python and JSON Schema; '$' alone admits a final newline.
TASK_ID_PATTERN = r"^T-[0-9]{4,}(?![\s\S])"

if sys.platform == "win32":
    fcntl = None
else:
    try:
        import fcntl
    except ImportError:  # Other platforms without flock.
        fcntl = None  # type: ignore[assignment]


def validate_task_id(task_id: str | None) -> str | None:
    if task_id is not None and (not isinstance(task_id, str) or re.fullmatch(TASK_ID_PATTERN, task_id) is None):
        raise ValueError("task_id must match T-[0-9]{4,}, for example T-0001")
    return task_id


@contextmanager
def _ledger_lock(root: Path) -> Iterator[None]:
    """Serialize cooperating writers; never unlink a flock file (inode identity matters)."""
    if fcntl is not None:
        with (root / ".evidence.lock").open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    # mkdir is atomic across processes too. Fail closed on stale locks rather
    # than guessing whether another writer is alive and risking a fork.
    lock_dir = root / ".evidence.lock.d"
    deadline = time.monotonic() + 30
    while True:
        try:
            lock_dir.mkdir()
            break
        except (FileExistsError, PermissionError) as exc:
            # Windows reports a lock directory that another writer is removing (delete pending) as
            # access denied: that is contention, not a failure. A real permission problem still
            # surfaces once the deadline passes.
            if isinstance(exc, PermissionError) and sys.platform != "win32":
                raise
            if time.monotonic() >= deadline:
                if isinstance(exc, PermissionError):
                    raise
                raise TimeoutError(f"Evidence lock timed out; check for a stale lock: {lock_dir}") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        lock_dir.rmdir()


def evidence_dir(path: str | None = None, *, project: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    if project:
        return Path(project).expanduser().resolve() / "runs"
    return Path.cwd().resolve() / "runs"


def ledger_path(root: Path) -> Path:
    return root / LEDGER_FILE


def _require_fields(value: object, label: str, **fields: type) -> dict[str, Any]:
    """Check decoded shapes before consumers access nested fields (no coercion)."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    for name, kind in fields.items():
        if not isinstance(value.get(name), kind):
            raise ValueError(f"{label}.{name} must be {kind.__name__}")
    return value


def _validate_work(value: object) -> None:
    _require_fields(
        value, "Work", schema_version=str, work_id=str, created_at_utc=str, task=str,
        repo=dict, route=dict, packet=dict, packet_preflight=dict, ai_quality=dict,
        verification_commands=list, memory_commands=list, audit=list, unknowns=list,
    )


def _validate_gates(value: object) -> None:
    _require_fields(value, "Gates", verification_gate=str, sidecar_eval_gate=str,
                    human_decision_gate=str, release_claim_gate=str)


def _validate_record(value: object) -> dict[str, Any]:
    record = _require_fields(
        value, "Record", artifact_kind=str, schema_version=str, event_id=str,
        previous_hash=str, created_at_utc=str, work=dict, provider=dict,
        verification=dict, quality=dict, gates=dict, claims=dict, unknowns=list,
        human_decision=dict, record_hash=str,
    )
    if record["artifact_kind"] != "hipson.evidence_record":
        raise ValueError("Record.artifact_kind must be hipson.evidence_record")
    _validate_work(record["work"])
    _validate_gates(record["gates"])
    _require_fields(record["claims"], "Claims", safe=list, unsafe=list, evidence_refs=list)
    if "summary" in record["quality"]:
        _require_fields(record["quality"], "Quality", summary=dict)
    if "status" in record["verification"]:
        _require_fields(record["verification"], "Verification", status=str)
    if "actor" in record:
        _require_fields(record, "Record", actor=str)
    if "task_id" in record:
        _require_fields(record, "Record", task_id=str)
        validate_task_id(record["task_id"])
    return record


def _decode_record(line: str) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON record") from None
    return _validate_record(value)


def read_records(root: Path, *, task_id: str | None = None) -> list[dict[str, Any]]:
    validate_task_id(task_id)
    path = ledger_path(root)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if line.strip():
            try:
                record = _decode_record(line)
            except ValueError as exc:
                raise ValueError(f"{path}: line {line_number}: {exc}") from None
            if task_id is None or record.get("task_id") == task_id:
                records.append(record)
    return records


def latest_hash(root: Path) -> str:
    records = read_records(root)
    return str(records[-1].get("record_hash", "")) if records else ""


def append_record(root: Path, record: dict[str, Any]) -> Path:
    """Finalize and append atomically; update the caller's record to the persisted payload."""
    validate_task_id(record.get("task_id"))
    if "actor" in record and not isinstance(record["actor"], str):
        raise ValueError("actor must be a string (an unauthenticated recorded label)")
    root.mkdir(parents=True, exist_ok=True)
    path = ledger_path(root)
    with _ledger_lock(root):
        payload = redact_value(record)
        payload["previous_hash"] = latest_hash(root)
        payload["record_hash"] = stable_hash({key: value for key, value in payload.items() if key != "record_hash"})
        _validate_record(payload)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        record.clear()
        record.update(payload)
    return path


def verify_chain(root: Path) -> dict[str, Any]:
    """Check stored bytes' decoded values without redacting or repairing old records."""
    errors: list[dict[str, Any]] = []
    count = 0
    previous_hash = ""
    path = ledger_path(root)
    if path.exists():
        with _ledger_lock(root):
            lines = path.read_text(encoding="utf-8").split("\n")
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            count += 1
            try:
                record = _decode_record(line)
            except ValueError as exc:
                errors.append({"line": line_number, "error": str(exc)})
                continue
            if record.get("previous_hash") != previous_hash:
                errors.append({"line": line_number, "error": "previous_hash does not match preceding record_hash"})
            expected = stable_hash({key: value for key, value in record.items() if key != "record_hash"})
            if record.get("record_hash") != expected:
                errors.append({"line": line_number, "error": "record_hash does not match record contents"})
            previous_hash = record.get("record_hash", "")
    return {"ledger": str(path), "ok": not errors, "record_count": count, "errors": errors}


def build_evidence_record(
    *,
    work_plan: dict[str, Any],
    verification: dict[str, Any] | None = None,
    quality_report: dict[str, Any] | None = None,
    quality_eval: dict[str, Any] | None = None,
    sidecar_report: str | None = None,
    ledger_root: Path,
    human_decision: str = "pending",
    actor: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    validate_task_id(task_id)
    if actor is not None and not isinstance(actor, str):
        raise ValueError("actor must be a string (an unauthenticated recorded label)")
    verification_payload = verification or {}
    quality_report_payload = quality_report or {}
    quality_eval_payload = quality_eval or {}
    work_id = str(work_plan.get("work_id", ""))
    report_errors = _quality_report_errors(work_id, verification_payload, quality_report_payload)
    effective_report = {} if report_errors else quality_report_payload
    provider_used = bool(sidecar_report or _ai_quality_enabled(work_plan))
    gates = _gate_summary(
        verification_payload,
        effective_report,
        quality_eval_payload,
        human_decision=human_decision,
        work_id=work_id,
    )
    if report_errors:
        gates["sidecar_eval_gate"] = "blocked"
        gates["release_claim_gate"] = "blocked"
    record: dict[str, Any] = {
        "artifact_kind": "hipson.evidence_record",
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id("evidence"),
        # append_record supplies the authoritative link and hash under its lock.
        "previous_hash": "",
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
        "gates": gates,
        "claims": {
            "safe": _safe_claims(work_plan, verification_payload, effective_report, quality_eval_payload, gates),
            "unsafe": _unsafe_claims(work_plan, verification_payload, quality_report_payload, quality_eval_payload, gates),
            "evidence_refs": [],
        },
        "unknowns": _unknowns(work_plan, verification_payload, effective_report, quality_eval_payload, gates) + report_errors,
        "human_decision": {"required": True, "outcome": redact_text(human_decision)},
    }
    if actor is not None:
        record["actor"] = actor
    if task_id is not None:
        record["task_id"] = task_id
    record = redact_value(record)
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


def audit_bundle(*, work_path: str, ledger_root: Path, task_id: str | None = None) -> dict[str, Any]:
    work_plan = load_work_plan(work_path)
    work_id = str(work_plan.get("work_id", ""))
    records = [
        record
        for record in read_records(ledger_root, task_id=task_id)
        if str(record.get("work", {}).get("work_id", "")) == work_id
    ]
    return _audit_bundle(work_run_from_plan(work_plan), records)


def latest_audit_bundle(project: Path) -> dict[str, Any]:
    """Read the newest project audit or derive one from evidence in project/runs."""
    candidates: list[tuple[int, dict[str, Any]]] = []
    candidate_errors: list[dict[str, str]] = []
    for path in (project / "runs").rglob("audit.json"):
        try:
            bundle = _validate_audit_bundle(json.loads(path.read_text(encoding="utf-8")))
            if _belongs_to_project(bundle["work"], project):
                candidates.append((path.stat().st_mtime_ns, bundle))
        except (OSError, ValueError) as exc:
            candidate_errors.append({"path": str(path), "error": redact_text(str(exc))})
    for path in (project / "runs").rglob(LEDGER_FILE):
        try:
            records = [record for record in read_records(path.parent) if _belongs_to_project(record["work"], project)]
            if records:
                latest = records[-1]
                work = latest["work"]
                matching = [record for record in records if record["work"]["work_id"] == work["work_id"]]
                candidates.append((path.stat().st_mtime_ns, _audit_bundle(work, matching)))
        except (OSError, ValueError) as exc:
            candidate_errors.append({"path": str(path), "error": redact_text(str(exc))})
    if candidates:
        result = max(candidates, key=lambda candidate: candidate[0])[1]
    else:
        result = _audit_bundle(work_run_from_plan({"repo_state": {"path": str(project)}}), [])
        result["project"] = str(project)
        result["unknowns"] = ["No readable audit bundle or evidence records found for this project under runs/."]
    if candidate_errors:
        result["candidate_errors"] = candidate_errors
        result["unknowns"] = [*result["unknowns"], "Corrupt or unreadable audit candidates found; see candidate_errors."]
    return result


def _validate_audit_bundle(value: object) -> dict[str, Any]:
    bundle = _require_fields(
        value, "Audit", artifact_kind=str, schema_version=str, work=dict, evidence_records=list,
        latest_status=str, latest_quality_gate=str, latest_gates=dict,
        latest_release_claim_gate=str, unknowns=list,
    )
    if bundle["artifact_kind"] != "hipson.audit_bundle" or "latest_eval_ok" not in bundle:
        raise ValueError("Expected hipson.audit_bundle with latest_eval_ok")
    _validate_work(bundle["work"])
    _validate_gates(bundle["latest_gates"])
    for record in bundle["evidence_records"]:
        _validate_record(record)
    return bundle


def _belongs_to_project(work: object, project: Path) -> bool:
    repo = work.get("repo") if isinstance(work, dict) else None
    path = repo.get("path") if isinstance(repo, dict) else None
    return isinstance(path, str) and bool(path) and Path(path).expanduser().resolve() == project.resolve()


def _audit_bundle(work: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    latest = records[-1] if records else None
    latest_quality = latest.get("quality", {}) if latest else {}
    latest_quality_summary = latest_quality.get("summary", {}) if isinstance(latest_quality, dict) else {}
    latest_gates = latest.get("gates", {}) if latest else dict.fromkeys(
        ("verification_gate", "sidecar_eval_gate", "human_decision_gate", "release_claim_gate"), "no-evidence"
    )
    return {
        "artifact_kind": "hipson.audit_bundle",
        "schema_version": SCHEMA_VERSION,
        "work": work,
        "evidence_records": records,
        "latest_status": latest.get("verification", {}).get("status", "no-evidence") if latest else "no-evidence",
        "latest_quality_gate": latest_quality_summary.get("quality_gate", "no-quality") if latest else "no-evidence",
        "latest_eval_ok": latest_quality_summary.get("eval_ok", "no-eval") if latest else "no-evidence",
        "latest_gates": latest_gates if isinstance(latest_gates, dict) else {},
        "latest_release_claim_gate": latest_gates.get("release_claim_gate", "no-evidence")
        if isinstance(latest_gates, dict)
        else "no-evidence",
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
    gates: dict[str, str],
) -> list[str]:
    claims = ["work plan generated locally"]
    if not _ai_quality_enabled(work_plan):
        claims.append("provider-free work planning")
    if gates.get("verification_gate") == "passed":
        claims.append("listed verification commands passed")
    if gates.get("verification_gate") == "passed":
        claims.append("verification gate passed")
    if quality_eval.get("ok") is True:
        claims.append("sidecar eval passed without local evidence conflicts")
    if gates.get("release_claim_gate") == "passed":
        claims.append("release claim gate passed")
    return claims


def _unsafe_claims(
    work_plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
    gates: dict[str, str],
) -> list[str]:
    claims = ["sidecar output correctness", "sidecar findings verified", "production release readiness"]
    if verification.get("status") != "passed":
        claims.append("verification passed")
    if quality_report and quality_report.get("quality_gate") != "passed":
        claims.append("verification gate passed")
    if quality_eval and quality_eval.get("ok") is not True:
        claims.append("sidecar eval passed")
    if gates.get("release_claim_gate") != "passed":
        claims.append("release claim gate passed")
    if _ai_quality_enabled(work_plan):
        claims.append("AI quality pass approved the work")
    return claims


def _unknowns(
    work_plan: dict[str, Any],
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
    gates: dict[str, str],
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
    if gates.get("sidecar_eval_gate") == "unverified":
        unknowns.append("Sidecar findings have not been verified against local evidence.")
    if gates.get("release_claim_gate") != "passed":
        unknowns.append("Release claim gate is not passed.")
    return unknowns


def _quality_summary(quality_report: dict[str, Any], quality_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_gate": str(quality_report.get("quality_gate", "missing")) if quality_report else "missing",
        "verification_gate": str(quality_report.get("verification_gate", "missing")) if quality_report else "missing",
        "sidecar_eval_gate": str(quality_report.get("sidecar_eval_gate", "missing")) if quality_report else "missing",
        "human_decision_gate": str(quality_report.get("human_decision_gate", "missing")) if quality_report else "missing",
        "release_claim_gate": str(quality_report.get("release_claim_gate", "missing")) if quality_report else "missing",
        "verification_status": str(quality_report.get("verification_status", "")) if quality_report else "",
        "sidecar_present": bool(quality_report.get("sidecar_present", False)) if quality_report else False,
        "finding_count": int(quality_report.get("sidecar", {}).get("finding_count", 0) or 0)
        if isinstance(quality_report.get("sidecar"), dict)
        else 0,
        "eval_ok": bool(quality_eval.get("ok")) if quality_eval else False,
        "eval_score": int(quality_eval.get("score", 0) or 0) if quality_eval else 0,
        "eval_issue_count": len(quality_eval.get("issues", [])) if isinstance(quality_eval.get("issues"), list) else 0,
    }


def _gate_summary(
    verification: dict[str, Any],
    quality_report: dict[str, Any],
    quality_eval: dict[str, Any],
    *,
    human_decision: str,
    work_id: str = "",
) -> dict[str, str]:
    results = verification.get("results")
    # Passing needs command evidence for this work: a bare {"status": "passed"} proves nothing.
    verification_ok = (
        verification.get("status") == "passed"
        and bool(work_id) and verification.get("work_id") == work_id
        and isinstance(results, list) and bool(results)
        and all(isinstance(result, dict) and result.get("exit_code") == 0 for result in results)
    )
    verification_gate = "passed" if verification_ok else "blocked"
    sidecar_eval_gate = str(quality_report.get("sidecar_eval_gate", "not_applicable"))
    if quality_eval and quality_eval.get("ok") is not True:
        sidecar_eval_gate = "blocked"
    human_decision_gate = _human_decision_gate(human_decision)
    release_claim_gate = "passed" if (
        verification_gate == "passed"
        and sidecar_eval_gate in {"passed", "not_applicable"}
        and human_decision_gate == "passed"
    ) else "blocked"
    return {
        "verification_gate": verification_gate,
        "sidecar_eval_gate": sidecar_eval_gate,
        "human_decision_gate": human_decision_gate,
        "release_claim_gate": release_claim_gate,
    }


def _quality_report_errors(
    work_id: str, verification: dict[str, Any], report: dict[str, Any],
) -> list[str]:
    if not report:
        return []
    errors = []
    if report.get("work_id") != work_id:
        errors.append("Quality report ignored: work_id does not match this work.")
    if "verification_status" in report and report["verification_status"] != verification.get("status"):
        errors.append("Quality report ignored: verification_status does not match current verification.")
    if "verification_id" in report and report["verification_id"] != verification.get("verification_id"):
        errors.append("Quality report ignored: verification_id does not match current verification.")
    if "verification_sha256" in report and report["verification_sha256"] != stable_hash(verification):
        errors.append("Quality report ignored: verification_sha256 does not match current verification.")
    return errors


def _human_decision_gate(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized in {"accepted", "approved", "pass", "passed", "release", "released", "merge", "merged"}:
        return "passed"
    if normalized in {"rejected", "blocked", "failed", "fail"}:
        return "blocked"
    return "pending"
