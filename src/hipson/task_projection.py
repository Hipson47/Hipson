"""Compact agent reads over recorded ledger evidence."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from hipson.ledger import FINDING_SEVERITIES, Ledger
from hipson.redaction import redact_text
from hipson.task_verification import argv_identity

VIEW_SCHEMA = 1
ListKind = Literal["task.board", "task.now"]
LIST_BUDGETS: dict[ListKind, int] = {"task.board": 32_000, "task.now": 48_000}
TERMINAL = ("done", "cancelled")
REASON_LIMIT = 5


class CheckProjection(TypedDict):
    command: list[str]
    state: str
    record_id: str | None
    exit_code: int | None
    recorded_at: str | None
    recorded_revision: str | None
    head_matches: bool | None


class AssignmentProjection(TypedDict):
    id: str
    role: str
    actor: str
    status: str
    ack_state: str
    started_at: str
    ended_at: str | None
    result_status: str | None
    summary: str | None


class ReviewProjection(TypedDict):
    state: str
    assignment_id: str | None


class TaskProjection(TypedDict):
    view_schema: int
    id: str
    project: str
    title: str
    objective: str
    status: str
    lead: str
    review_policy: str
    checks: list[CheckProjection]
    freshness_source: Literal["recorded"]
    freshness_note: str
    review: ReviewProjection
    assignments: list[AssignmentProjection]
    open_findings: dict[str, int]
    blocking_findings: list[str]
    major_findings: list[str]
    reasons: list[str]
    omitted_reasons: int
    truncated_fields: list[str]
    created_at: str
    updated_at: str
    last_activity_at: str


class ListProjection(TypedDict):
    view_schema: int
    kind: ListKind
    tasks: Sequence[TaskProjection]
    truncated: bool
    omitted: int
    more: dict[str, str | int] | None


def _head(project: str) -> str | None:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "protocol.allow=never", "-C", project, "rev-parse", "--verify", "HEAD"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=1, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha.decode("ascii") if re.fullmatch(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha) else None


def _bound(text: str, limit: int, field: str, truncated: list[str]) -> str:
    text = redact_text(text)
    if len(text) <= limit:
        return text
    truncated.append(field)
    return text[:limit - 1] + "…"


def _check_reasons(record: dict[str, Any] | None, lead: str, builder_seq: int) -> list[tuple[str, str]]:
    if record is None:
        return [("missing", "No recorded verification")]
    reasons = []
    if record["state_changed"]:
        reasons.append(("stale", "Project changed during verification or cleanup was unconfirmed"))
    if (record["builder_finished_seq"] or 0) < builder_seq:
        reasons.append(("stale", "Verification predates the latest finished builder assignment"))
    if record["exit_code"] != 0:
        reasons.append(("failing", f"Verification exit code: {record['exit_code']}"))
    if record["partial"]:
        reasons.append(("partial", "Verification recorded partial content coverage"))
    if not record["fingerprint_available"]:
        error = record["error"] or "cause unavailable"
        reasons.append(("unknown", f"Verification recorded no working-tree fingerprint: {error}"))
    if record["cleanup_confirmed"] == 0:
        reasons.append(("unknown", "Verification process cleanup was unconfirmed"))
    context = json.loads(record["context"]) if record["context"] else None
    if (record["actor"] != lead or not isinstance(context, dict)
            or set(context) != {"role", "assignment", "task"} or context["assignment"] is not None
            or str(context["role"] or "").strip().lower() == "worker"):
        reasons.append(("unknown", "Verification was recorded without the task lead's verification context"))
    return reasons


def _checks(row: dict[str, Any], head: str | None, reasons: list[str]) -> list[CheckProjection]:
    records = row["verifications"]
    latest = {record["argv_sha256"]: record for record in records}
    pairs = [(command, latest.get(argv_identity(command))) for command in row["checks"]]
    if not pairs and records:
        pairs = [(json.loads(records[-1]["command"]), records[-1])]
    checks = []
    for number, (command, record) in enumerate(pairs, 1):
        problems = _check_reasons(record, row["lead"], row["builder_finished_seq"])
        reasons.extend(f"Check {number}: {reason}" for _, reason in problems)
        revision = record["revision"] if record else None
        checks.append(CheckProjection(
            command=[redact_text(arg) for arg in command],
            state=problems[0][0] if problems else "passed",
            record_id=record["id"] if record else None,
            exit_code=record["exit_code"] if record else None,
            recorded_at=record["recorded_at"] if record else None,
            recorded_revision=revision,
            head_matches=head == revision if head and revision and revision != "none" else None,
        ))
    return checks


def _review(row: dict[str, Any]) -> ReviewProjection:
    assigned = row["review_assignments"]
    reviewer = max((item for item in row["assignments"] if item["role"] == "reviewer"),
                   key=lambda item: assigned.get(item["id"], 0), default=None)
    if reviewer is None:
        state = "missing" if row["review_policy"] == "required" else "not_required"
        return {"state": state, "assignment_id": None}
    assignment = reviewer["id"]
    trees = row["review_trees"].get(assignment, {})
    start, end = trees.get("start"), trees.get("end")
    if reviewer["status"] == "active":
        state = "pending"
    elif reviewer["status"] != "passed" or reviewer["result_status"] != "completed" or not start or not end:
        state = "incomplete"
    elif start[2] != "snapshot" or end[2] != "snapshot":
        state = "live"
    elif start != end:
        state = "changed"
    else:
        state = "snapshot_recorded"
    return {"state": state, "assignment_id": assignment}


def _activity_order(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is not None:
            return stamp
    except (ValueError, OverflowError):
        pass
    return datetime.min.replace(tzinfo=UTC)


def _project(row: dict[str, Any], head: str | None) -> TaskProjection:
    truncated: list[str] = []
    reasons: list[str] = []
    checks = _checks(row, head, reasons)
    assignments = []
    activity = [row["updated_at"], row["created_at"], row["last_event_at"]]
    activity.extend(record["recorded_at"] for record in row["verifications"])
    for index, item in enumerate(row["assignments"]):
        summary = item["result_summary"]
        assignments.append(AssignmentProjection(
            id=item["id"], role=item["role"], actor=redact_text(item["actor"]), status=item["status"],
            ack_state=item["ack_state"], started_at=item["started_at"], ended_at=item["ended_at"],
            result_status=item["result_status"],
            summary=_bound(" ".join(summary.splitlines()), 200, f"assignments.{index}.summary", truncated)
            if summary is not None else None,
        ))
        activity.extend((item["started_at"], item["ended_at"], item["heartbeat_at"]))
    counts = dict.fromkeys(FINDING_SEVERITIES, 0)
    for finding in row["findings"]:
        counts[finding["severity"]] += 1
    return TaskProjection(
        view_schema=VIEW_SCHEMA, id=row["id"], project=redact_text(row["project"]), status=row["status"],
        lead=redact_text(row["lead"]), review_policy=row["review_policy"],
        title=_bound(row["title"], 160, "title", truncated),
        objective=_bound(row["objective"], 400, "objective", truncated),
        checks=checks, freshness_source="recorded",
        freshness_note="For a content re-check, run task verify or read the verification section.",
        review=_review(row), assignments=assignments, open_findings=counts,
        blocking_findings=[item["id"] for item in row["findings"] if item["severity"] == "blocking"],
        major_findings=[item["id"] for item in row["findings"] if item["severity"] == "major"],
        reasons=[_bound(reason, 240, f"reasons.{index}", truncated)
                 for index, reason in enumerate(reasons[:REASON_LIMIT])],
        omitted_reasons=max(0, len(reasons) - REASON_LIMIT), truncated_fields=truncated,
        created_at=row["created_at"], updated_at=row["updated_at"],
        last_activity_at=max(filter(None, activity), key=_activity_order),
    )


def project_task(store: Ledger, task: str) -> TaskProjection:
    row, = store.projection_rows(task=task)
    return _project(row, _head(row["project"]))


def _now_priority(task: TaskProjection) -> int:
    if any(item["ack_state"] == "unacknowledged" for item in task["assignments"]):
        return 0
    if task["status"] in TERMINAL:
        return 5
    if task["status"] == "blocked" or task["blocking_findings"] or task["major_findings"]:
        return 1
    if any(item["status"] == "active" for item in task["assignments"]):
        return 2
    return 3 if task["status"] == "review" else 4


def _fit(tasks: list[TaskProjection], kind: ListKind, budget: int) -> ListProjection:
    # Default JSON dumps uses ASCII escapes and ", " between items. Size the
    # envelope separately so trimming the suffix serializes each task only once.
    sizes = [len(json.dumps(task)) for task in tasks]
    payload_size = sum(sizes) + 2 * max(0, len(tasks) - 1)
    count = len(tasks)
    while count >= 0:
        omitted = len(tasks) - count
        view = ListProjection(view_schema=VIEW_SCHEMA, kind=kind, tasks=[], truncated=omitted > 0,
                              omitted=omitted, more={"kind": kind, "offset": count} if omitted else None)
        if len(json.dumps(view)) + payload_size <= budget:
            view["tasks"] = tasks[:count]
            return view
        if count:
            payload_size -= sizes[count - 1] + (2 if count > 1 else 0)
        count -= 1
    raise ValueError("Byte budget is too small for the projection envelope")


def project_list(store: Ledger, *, kind: ListKind, project: str | None = None,
                 include_terminal: bool = False, byte_budget: int | None = None) -> ListProjection:
    """Project tasks in board creation order or now attention order."""
    rows = store.projection_rows(project=project, include_terminal=include_terminal)
    heads = {project: _head(project) for project in dict.fromkeys(row["project"] for row in rows)}
    tasks = [_project(row, heads[row["project"]]) for row in rows]
    if kind == "task.now":
        tasks.sort(key=lambda task: (_now_priority(task), task["id"]))
    return _fit(tasks, kind, LIST_BUDGETS[kind] if byte_budget is None else byte_budget)
