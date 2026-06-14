"""Local quality report correlation for AI-dev work artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hipson import output_policy
from hipson.contracts import sha256_text, timestamp
from hipson.redaction import redact_text, sanitize_path
from hipson.verification import load_work_plan

MAX_SIDECAR_EXCERPT_CHARS = 4000
MAX_FINDING_SUMMARY_CHARS = 500
METADATA_BLOCK_RE = re.compile(r"## Metadata\s*```json\s*(\{.*?\})\s*```", re.DOTALL)
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
FINDING_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:finding|issue)\s*(?:\[(?P<id>[A-Za-z0-9_.:-]+)\])?\s*[:#-]\s*(?P<summary>.+?)\s*$"
)


def build_quality_report(
    *,
    work_path: str | Path,
    verification_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
    decision: str = "pending",
) -> dict[str, Any]:
    work = load_work_plan(work_path)
    verification = _load_optional_json(verification_path)
    sidecar = _sidecar_summary(sidecar_path)
    verification_status = str(verification.get("status", "missing")) if verification else "missing"
    sidecar_present = bool(sidecar)
    ok = verification_status == "passed"
    gate_summary = _gates(
        verification_status=verification_status,
        sidecar_present=sidecar_present,
        decision=decision,
    )
    return {
        "artifact_kind": "hipson.quality_report",
        "schema_version": "1.0",
        "created_at_utc": timestamp(),
        "work_id": str(work.get("work_id", "")),
        "task": redact_text(str(work.get("task", ""))),
        "project": str(work.get("project", "")),
        "ok": ok,
        "quality_gate": gate_summary["verification_gate"],
        "verification_gate": gate_summary["verification_gate"],
        "sidecar_eval_gate": gate_summary["sidecar_eval_gate"],
        "human_decision_gate": gate_summary["human_decision_gate"],
        "release_claim_gate": gate_summary["release_claim_gate"],
        "gates": gate_summary,
        "verification_status": verification_status,
        "sidecar_present": sidecar_present,
        "ai_quality": _ai_quality_summary(work),
        "sidecar_metadata": sidecar.get("metadata", {}) if sidecar else {},
        "decision": redact_text(decision),
        "summary": _summary(verification_status, sidecar_present, decision),
        "verified_findings": _verified_findings(verification),
        "advisory_findings": _advisory_findings(sidecar),
        "finding_adjudication": _finding_adjudication(sidecar, verification_status),
        "rejected_or_unverified": _unverified(verification_status, sidecar_present),
        "required_local_checks": _required_checks(verification_status),
        "sidecar": sidecar,
    }


def write_report(
    report: dict[str, Any],
    output: str | Path,
    *,
    cwd: str | Path | None = None,
    allow_unsafe_output: bool = False,
) -> Path:
    path = output_policy.resolve_output_path(
        output,
        cwd=cwd,
        allow_unsafe=allow_unsafe_output,
        description="quality report output",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Hipson Quality Report",
        "",
        f"- Work ID: `{report['work_id']}`",
        f"- Quality gate: `{report['quality_gate']}`",
        f"- Verification gate: `{report['verification_gate']}`",
        f"- Sidecar eval gate: `{report['sidecar_eval_gate']}`",
        f"- Human decision gate: `{report['human_decision_gate']}`",
        f"- Release claim gate: `{report['release_claim_gate']}`",
        f"- Verification: `{report['verification_status']}`",
        f"- Sidecar present: `{str(report['sidecar_present']).lower()}`",
        f"- Decision: `{report['decision']}`",
        *_metadata_lines(report),
        "",
        "## Summary",
        str(report["summary"]),
        "",
        "## Verified Findings",
        *_bullets(report["verified_findings"]),
        "",
        "## Advisory Findings",
        *_bullets(report["advisory_findings"]),
        "",
        "## Finding Adjudication",
        *_bullets(report["finding_adjudication"]),
        "",
        "## Rejected Or Unverified",
        *_bullets(report["rejected_or_unverified"]),
        "",
        "## Required Local Checks",
        *_bullets(report["required_local_checks"]),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _load_optional_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    artifact = Path(path).expanduser().resolve()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"JSON artifact must be an object: {artifact}")
    return data


def _sidecar_summary(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    sidecar_path = Path(path).expanduser().resolve()
    raw = sidecar_path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_text(raw)
    excerpt = redacted[:MAX_SIDECAR_EXCERPT_CHARS]
    if len(redacted) > MAX_SIDECAR_EXCERPT_CHARS:
        excerpt = excerpt.rstrip() + f"\n[sidecar excerpt truncated to {MAX_SIDECAR_EXCERPT_CHARS} chars]"
    metadata = _sidecar_metadata(redacted)
    findings = parse_sidecar_findings(redacted)
    return {
        "path": sanitize_path(sidecar_path),
        "sha256": sha256_text(redacted),
        "excerpt": excerpt,
        "advisory": True,
        "metadata": metadata,
        "findings": findings,
        "finding_count": len(findings),
    }


def _summary(verification_status: str, sidecar_present: bool, decision: str) -> str:
    if verification_status == "passed" and sidecar_present:
        return "Local verification passed; sidecar output is available as advisory review data."
    if verification_status == "passed":
        return "Local verification passed; no sidecar output was attached."
    if sidecar_present:
        return "Sidecar output is present, but local verification is missing or failed."
    return "No sidecar output is attached and local verification is missing or failed."


def _gates(*, verification_status: str, sidecar_present: bool, decision: str) -> dict[str, str]:
    verification_gate = "passed" if verification_status == "passed" else "blocked"
    sidecar_eval_gate = "unverified" if sidecar_present else "not_applicable"
    human_decision_gate = _human_decision_gate(decision)
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


def _human_decision_gate(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized in {"accepted", "approved", "pass", "passed", "release", "released", "merge", "merged"}:
        return "passed"
    if normalized in {"rejected", "blocked", "failed", "fail"}:
        return "blocked"
    return "pending"


def _verified_findings(verification: dict[str, Any] | None) -> list[str]:
    if not verification:
        return []
    results = verification.get("results", [])
    if not isinstance(results, list):
        return []
    return [
        f"{item.get('command', '')}: {item.get('status', '')}"
        for item in results
        if isinstance(item, dict) and item.get("exit_code") == 0
    ]


def _advisory_findings(sidecar: dict[str, Any]) -> list[str]:
    if not sidecar:
        return []
    findings = sidecar.get("findings", [])
    if isinstance(findings, list) and findings:
        return [
            f"{item.get('id', 'SIDE-???')}: {item.get('summary', '')}"
            for item in findings
            if isinstance(item, dict)
        ]
    return ["Sidecar report attached; review excerpt and hash before accepting any finding."]


def _finding_adjudication(sidecar: dict[str, Any], verification_status: str) -> list[str]:
    if not sidecar:
        return []
    findings = sidecar.get("findings", [])
    if not isinstance(findings, list) or not findings:
        return ["No structured sidecar findings were detected; keep the report excerpt advisory."]
    status = "unverified"
    reason = "requires file-level review and targeted local checks"
    if verification_status != "passed":
        status = "blocked"
        reason = "local verification is missing or failed"
    return [
        f"{item.get('id', 'SIDE-???')}: {status} - {reason}"
        for item in findings
        if isinstance(item, dict)
    ]


def _unverified(verification_status: str, sidecar_present: bool) -> list[str]:
    items = []
    if verification_status != "passed":
        items.append("Verification is not passed; no correctness claim is supported.")
    if sidecar_present:
        items.append("Sidecar findings are advisory until checked against local files and tests.")
    if sidecar_present:
        items.append("Structured sidecar findings without matching local evidence remain unverified.")
    return items


def _required_checks(verification_status: str) -> list[str]:
    if verification_status == "passed":
        return ["Human review remains required before release or merge claims."]
    return ["Run or fix local verification before using this report for handoff."]


def _bullets(items: object) -> list[str]:
    values = items if isinstance(items, list) else []
    if not values:
        return ["- none"]
    return [f"- {item}" for item in values]


def _ai_quality_summary(work: dict[str, Any]) -> dict[str, str]:
    ai_quality = work.get("ai_quality", {})
    if not isinstance(ai_quality, dict):
        return {}
    return {
        "profile": redact_text(str(ai_quality.get("profile", ""))),
        "agent": redact_text(str(ai_quality.get("agent", ""))),
        "model": redact_text(str(ai_quality.get("model", ""))),
    }


def _metadata_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    ai_quality = report.get("ai_quality", {})
    if isinstance(ai_quality, dict) and any(ai_quality.values()):
        lines.append(f"- AI profile: `{ai_quality.get('profile') or 'none'}`")
        lines.append(f"- AI agent: `{ai_quality.get('agent') or 'none'}`")
        lines.append(f"- AI model: `{ai_quality.get('model') or 'none'}`")
    sidecar_metadata = report.get("sidecar_metadata", {})
    if isinstance(sidecar_metadata, dict) and any(sidecar_metadata.values()):
        lines.append(f"- Sidecar agent: `{sidecar_metadata.get('agent') or 'unknown'}`")
        lines.append(f"- Sidecar model: `{sidecar_metadata.get('model') or 'unknown'}`")
    return lines


def _sidecar_metadata(text: str) -> dict[str, str]:
    match = METADATA_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "schema_version": redact_text(str(payload.get("schema_version", ""))),
        "agent": redact_text(str(payload.get("agent", ""))),
        "model": redact_text(str(payload.get("model", ""))),
        "packet": sanitize_path(redact_text(str(payload.get("packet", "")))),
    }


def parse_sidecar_findings(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in JSON_BLOCK_RE.findall(text):
        findings.extend(_findings_from_json_block(block, start_index=len(findings) + 1))
    for match in FINDING_LINE_RE.finditer(text):
        item = _finding_payload(match.group("id"), match.group("summary"), len(findings) + 1)
        if item["summary"] not in seen:
            findings.append(item)
            seen.add(item["summary"])
    return findings


def _findings_from_json_block(block: str, *, start_index: int) -> list[dict[str, str]]:
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        return []
    findings = []
    for offset, raw in enumerate(raw_findings):
        if not isinstance(raw, dict):
            continue
        summary = raw.get("summary") or raw.get("title") or raw.get("message") or raw.get("finding") or ""
        item = _finding_payload(raw.get("id"), str(summary), start_index + offset)
        item["file"] = sanitize_path(redact_text(str(raw.get("file", ""))))
        item["severity"] = redact_text(str(raw.get("severity", "")))
        findings.append(item)
    return findings


def _finding_payload(raw_id: object, summary: str, index: int) -> dict[str, str]:
    finding_id = redact_text(str(raw_id or "")).strip() or f"SIDE-{index:03d}"
    bounded_summary = redact_text(summary).strip()
    if len(bounded_summary) > MAX_FINDING_SUMMARY_CHARS:
        marker = f"... [truncated to {MAX_FINDING_SUMMARY_CHARS} chars]"
        bounded_summary = bounded_summary[: max(0, MAX_FINDING_SUMMARY_CHARS - len(marker))].rstrip() + marker
    return {
        "id": finding_id,
        "summary": bounded_summary,
        "status": "advisory",
        "file": "",
        "severity": "",
    }
