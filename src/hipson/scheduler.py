"""Opt-in local scheduler tick for Hipson runtime jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hipson.approvals import ApprovalDecision, ApprovalPolicy
from hipson.redaction import redact_text
from hipson.session import SessionStore, timestamp
from hipson.tools import ToolContext, ToolRegistry, ToolRegistryError, ToolSpec, build_default_registry


@dataclass(frozen=True)
class SchedulerResult:
    job_id: str
    status: str
    summary: str
    error: str = ""


@dataclass
class Scheduler:
    store: SessionStore
    registry: ToolRegistry
    approval_policy: ApprovalPolicy

    @classmethod
    def with_defaults(cls, store: SessionStore) -> Scheduler:
        return cls(store=store, registry=build_default_registry(), approval_policy=ApprovalPolicy())

    def create_tool_job(
        self,
        *,
        tool_name: str,
        input_data: dict[str, object],
        run_after: str | None = None,
        approved: bool = False,
        schedule: str = "",
    ) -> str:
        payload: dict[str, object] = {
            "tool": tool_name,
            "input": input_data,
            "approved": approved,
        }
        return self.store.add_job(kind="tool", payload=payload, schedule=schedule, run_after=run_after)

    def list_due_jobs(self, *, now: str | None = None, limit: int = 20) -> list[dict[str, object]]:
        return self.store.list_due_jobs(now=now, limit=limit)

    def tick(self, *, cwd: Path, now: str | None = None, limit: int = 20) -> list[SchedulerResult]:
        effective_now = now or timestamp()
        results: list[SchedulerResult] = []
        for job in self.store.list_due_jobs(now=effective_now, limit=limit):
            results.append(self._run_job(job, cwd=cwd, now=effective_now))
        return results

    def _run_job(self, job: dict[str, object], *, cwd: Path, now: str) -> SchedulerResult:
        payload = _payload(job)
        job_id = str(job["id"])
        if job.get("kind") != "tool":
            return self._fail(job_id, payload, now, "Unsupported scheduler job kind")
        tool_name = payload.get("tool")
        input_data = payload.get("input")
        if not isinstance(tool_name, str) or not isinstance(input_data, dict):
            return self._fail(job_id, payload, now, "Tool job payload must include tool and input")
        try:
            spec = self.registry.get(tool_name)
        except ToolRegistryError as exc:
            return self._fail(job_id, payload, now, str(exc))

        approved = bool(payload.get("approved", False))
        if spec.risk_level in {"external", "exec", "dangerous"}:
            return self._fail(job_id, payload, now, f"Scheduler does not run {spec.risk_level} jobs")
        decision = self._decision(spec, input_data, cwd=cwd, approved=approved)
        if not decision.allowed:
            return self._fail(job_id, payload, now, decision.reason)
        if spec.risk_level != "read" and not approved:
            return self._fail(job_id, payload, now, "Non-read scheduler jobs require explicit approval")

        try:
            result = self.registry.run(
                tool_name,
                input_data,
                ToolContext(cwd=cwd.resolve(), repo_root=None, session_id=f"scheduler:{job_id}"),
            )
        except ToolRegistryError as exc:
            return self._fail(job_id, payload, now, str(exc))

        if not result.ok:
            return self._fail(job_id, {**payload, "last_result": result.output}, now, result.error or result.summary)
        stored_payload = {**payload, "last_result": result.output, "last_summary": result.summary}
        self.store.update_job(job_id, status="completed", payload=stored_payload, last_run_at=now)
        return SchedulerResult(job_id=job_id, status="completed", summary=redact_text(result.summary))

    def _decision(self, spec: ToolSpec, input_data: dict[str, object], *, cwd: Path, approved: bool) -> ApprovalDecision:
        context = ToolContext(cwd=cwd.resolve(), repo_root=None, session_id="scheduler")
        return self.approval_policy.evaluate_tool(spec, input_data, context, approved=approved)

    def _fail(self, job_id: str, payload: dict[str, object], now: str, error: str) -> SchedulerResult:
        redacted_error = redact_text(error)
        self.store.update_job(
            job_id,
            status="failed",
            payload={**payload, "last_error": redacted_error},
            last_run_at=now,
        )
        return SchedulerResult(job_id=job_id, status="failed", summary="Job failed", error=redacted_error)


def parse_json_object(value: str) -> dict[str, object]:
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _payload(job: dict[str, object]) -> dict[str, object]:
    payload = job.get("payload")
    return payload if isinstance(payload, dict) else {}
