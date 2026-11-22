"""Compact agent reads over recorded ledger evidence."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, TypeVar, cast

from hipson import __version__
from hipson.display import escape_display_controls
from hipson.lead_context import GIT_READ_LIMIT
from hipson.ledger import CHECK_ARG_LIMIT, FINDING_SEVERITIES, MAX_CHECKS, Assignment, Ledger, review_state
from hipson.redaction import redact_text, redact_value
from hipson.task_activity import LINE_LIMIT
from hipson.task_calls import ReadCall
from hipson.task_calls import read_call as _read_call
from hipson.task_context import EVIDENCE_POLICY
from hipson.task_verification import _worker_context, argv_identity

if TYPE_CHECKING:
    from hipson.next_actions import NextAction

VIEW_SCHEMA = 1
ListKind = Literal["task.board", "task.now"]
LIST_BUDGETS: dict[ListKind, int] = {"task.board": 32_000, "task.now": 48_000}
RESUME_BUDGET = 64_000
SECTION_BUDGET = 64_000
MAX_CURSOR = 2**53 - 1  # Exact in MCP clients using JavaScript numbers.
MAX_LIMIT = 100
SECTION_LIMIT = 5
SECTION_FILE_LIMIT = 20
Section = Literal["verification", "assignments", "findings", "events"]
SECTIONS: tuple[Section, ...] = ("verification", "assignments", "findings", "events")
TASK_UNTRUSTED_FIELDS = ("assignments[].actor", "assignments[].summary")
SECTION_UNTRUSTED_FIELDS = {
    # The fields task_verification marks untrusted in each stored evidence record.
    "verification": ("actor", "context", "metadata_fallback_paths", "label", "command", "subject", "output_tail",
                     "error", "files", "generated_files", "changes"),
    "assignments": ("actor", "result", "activity", "failure_journal", "changes"),
    "findings": ("title", "detail", "location", "resolution_note"),
    "events": ("actor", "type", "summary", "refs"),
}
TERMINAL = ("done", "cancelled")
GATE_NOTE = "task verify / task close evaluate the gates"
REASON_LIMIT = 5
ASSIGNMENT_LIMIT = 8
GIT_TIMEOUT = 1
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
T = TypeVar("T")
Recorded = Literal["ok", "failing", "partial", "unknown", "missing"]
Current = Literal["stale", "not_checked", "unknown"]
SelectedRecord = tuple[int, str | None, dict[str, Any] | None]


class CheckProjection(TypedDict):
    command: list[str]
    recorded: Recorded
    current: Current
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
    hipson_version: str
    gate: Literal["not_evaluated"]
    gate_note: str
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
    omitted_assignments: int
    open_findings: dict[str, int]
    blocking_findings: list[str]
    major_findings: list[str]
    reasons: list[str]
    omitted_reasons: int
    truncated_fields: list[str]
    created_at: str
    updated_at: str
    last_activity_at: str


class TaskStub(TypedDict):
    id: str
    status: str
    title: str
    too_large: Literal[True]
    hint: str


class ListProjection(TypedDict):
    view_schema: int
    hipson_version: str
    gate: Literal["not_evaluated"]
    gate_note: str
    evidence_policy: str
    untrusted_fields: list[str]
    kind: ListKind
    tasks: Sequence[TaskProjection | TaskStub]
    truncated: bool
    omitted: int
    more: ReadCall | None
    next_actions: NotRequired[list[NextAction]]
    next_actions_omitted: NotRequired[int]


def dumps(obj: object) -> str:
    """Serialize a compact view. Budgets count the UTF-8 bytes of this string."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # Git paths can contain bytes that Python represents as surrogate escapes.
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _size(obj: object) -> int:
    return len(dumps(obj).encode("utf-8"))


def _git(project: str, *args: str) -> bytes | None:
    # Match lead_context._git hardening, with the compact read's shorter timeout.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", "--no-pager", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never", "-C", project, *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return result.stdout if result.returncode == 0 and len(result.stdout) <= GIT_READ_LIMIT else None


def _head(project: str) -> str | None:
    raw = _git(project, "rev-parse", "--verify", "HEAD")
    sha = raw.strip().decode("ascii", errors="replace") if raw else ""
    return sha if REVISION.fullmatch(sha) else None


def _observe(projects: Sequence[str], read: Callable[[str], T]) -> dict[str, T]:
    if not projects:
        return {}
    pool = ThreadPoolExecutor(max_workers=min(4, len(projects)))
    try:
        pending = {project: pool.submit(read, project) for project in projects}
        done, _ = wait(pending.values(), timeout=GIT_TIMEOUT)
        return {project: future.result() for project, future in pending.items() if future in done}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _text(value: str) -> str:
    return escape_display_controls(redact_text(value))


def _bound(value: str, limit: int, field: str, truncated: list[str]) -> str:
    value = _text(value)
    if len(value) <= limit:
        return value
    truncated.append(field)
    return value[:limit - 1] + "…"


def _command(argv: list[str], index: int, truncated: list[str]) -> list[str]:
    command: list[str] = []
    available = 400
    shortened = False
    for arg in argv:
        if available <= 0:
            shortened = True
            break
        arg = _text(arg)
        limit = min(200, available)
        if len(arg) > limit:
            arg = arg[:limit - 1] + "…"
            shortened = True
        command.append(arg)
        available -= len(arg) + 1  # Space between arguments.
    if shortened:
        truncated.append(f"checks.{index}.command")
    return command


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64 or not value.isprintable():
        return None
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(UTC) if stamp.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _argv(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(part, str) and len(part) <= CHECK_ARG_LIMIT for part in value
    )


def _record(row: dict[str, Any]) -> dict[str, Any] | None:
    fields = row["fields"]
    if (fields is None or row["duplicate_fields"] or row["malformed_fallback"] or row["malformed_files"]
            or row["fallback_type"] not in (None, "array") or row["files_type"] not in (None, "array")):
        return None
    # The SQLite query projects these fields in the same order and keeps JSON types intact.
    keys = ("id", "command", "finished_at", "revision", "exit_code", "builder_finished_seq",
            "state_changed", "actor", "context", "cleanup_confirmed", "fingerprint", "coverage", "error")
    try:
        record = dict(zip(keys, json.loads(fields.decode("utf-8")), strict=True))
    except (ValueError, RecursionError):
        return None
    record_id, exit_code, revision = record["id"], record["exit_code"], record["revision"]
    builder_seq, context, error = record["builder_finished_seq"], record["context"], record["error"]
    cleanup, fingerprint = record["cleanup_confirmed"], record["fingerprint"]
    if (not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", record_id) or record_id != row["id"]
            or type(exit_code) is not int or not -(2**31) <= exit_code < 2**32
            or not isinstance(revision, str) or (revision != "none" and not REVISION.fullmatch(revision))
            or _timestamp(record["finished_at"]) is None or not _argv(record["command"])
            or type(builder_seq) is not int or not 0 <= builder_seq <= 2**63 - 1
            or type(record["state_changed"]) is not bool
            or (cleanup is not None and type(cleanup) is not bool)
            or not isinstance(record["actor"], str)
            or not isinstance(context, dict) or set(context) != {"role", "assignment", "task"}
            or any(value is not None and (not isinstance(value, str) or len(value) > LINE_LIMIT)
                   for value in context.values())
            or (fingerprint is not None and (not isinstance(fingerprint, str) or len(fingerprint) > 64))
            or (error is not None and (not isinstance(error, str) or len(error) > LINE_LIMIT))
            or record["coverage"] not in (None, "complete", "partial")):
        return None
    record["metadata_fallback"] = bool(row["metadata_fallback"])
    return record


RECORDED_RANK: dict[str, int] = {"failing": 0, "partial": 1, "unknown": 2, "missing": 3}
NO_VERIFICATION = "No lead verification recorded; run hipson task verify"


def _recorded_issues(record: dict[str, Any], lead: str) -> list[tuple[Recorded, str]]:
    issues: list[tuple[Recorded, str]] = []
    if not record["fingerprint"]:
        detail = record["error"] or "cause unavailable"
        issues.append(("unknown", f"Verification recorded no working-tree fingerprint: {detail}"))
    if record["exit_code"] != 0:
        issues.append(("failing", f"Verification exit code: {record['exit_code']}"))
    if record["coverage"] == "partial" or record["metadata_fallback"]:
        issues.append(("partial", "Verification recorded partial content coverage"))
    elif record["coverage"] is None:
        issues.append(("unknown", "Verification did not record content coverage"))
    if record["cleanup_confirmed"] is not True:
        issues.append(("unknown", "Verification process cleanup was unconfirmed"))
    if record["actor"] != lead or _worker_context(record["context"]):
        issues.append(("unknown", "Verification was recorded without the task lead's verification context"))
    return issues


def _stale_reasons(record: dict[str, Any], builder_seq: int, head_matches: bool | None) -> list[str]:
    reasons = []
    if head_matches is False:
        reasons.append("HEAD moved since verification")
    if record["state_changed"]:
        reasons.append("Project changed during verification")
    if record["builder_finished_seq"] < builder_seq:
        reasons.append("Verification predates the latest finished builder assignment")
    return reasons


def _checks(row: dict[str, Any], records: list[SelectedRecord], head: str | None,
            reasons: list[str], truncated: list[str]) -> list[CheckProjection]:
    latest = {identity: (seq, item) for seq, identity, item in records if identity is not None}
    ambiguous = max((seq for seq, identity, _ in records if identity is None), default=0)
    declared = row["checks"]
    pairs = [(command, *latest.get(argv_identity(command), (0, None))) for command in declared[:MAX_CHECKS]]
    if len(declared) > MAX_CHECKS:
        truncated.append("checks")
    if not declared and records:
        seq, _, record = records[-1]
        pairs = [(record["command"] if record is not None else [], seq, record)]
    checks = []
    for index, (command, seq, record) in enumerate(pairs):
        issues: list[tuple[Recorded, str]]
        stale: list[str] = []
        if (declared and ambiguous > seq) or (record is None and seq > 0):
            record = None
            issues = [("unknown", "Recorded verification is malformed or has no usable identity")]
        elif record is None:
            issues = [("missing", "No recorded verification")]
        else:
            # Most severe first, so `recorded` and the bounded reason list never hide a failing exit code.
            issues = sorted(_recorded_issues(record, row["lead"]), key=lambda issue: RECORDED_RANK[issue[0]])
        revision = record["revision"] if record is not None else None
        matches = head == revision if head is not None and revision is not None else None
        if record is not None:
            stale = _stale_reasons(record, row["builder_finished_seq"], matches)
        reasons.extend(f"Check {index + 1}: {reason}" for reason in stale)
        reasons.extend(f"Check {index + 1}: {reason}" for _, reason in issues)
        checks.append(CheckProjection(
            command=_command(command, index, truncated),
            recorded=issues[0][0] if issues else "ok",
            current="stale" if stale else "unknown" if head is None else "not_checked",
            record_id=_text(record["id"]) if record else None,
            exit_code=record["exit_code"] if record else None,
            recorded_at=record["finished_at"] if record else None,
            recorded_revision=revision,
            head_matches=matches,
        ))
    if not pairs:
        # No declared checks and no records: the gate reports a missing lead verification, so the view must too.
        reasons.append(NO_VERIFICATION)
    return checks


def _review(row: dict[str, Any], records: list[SelectedRecord]) -> ReviewProjection:
    assigned = row["assigned"]
    reviewer = max((item for item in row["assignments"] if item["role"] == "reviewer"),
                   key=lambda item: assigned.get(item["id"], 0), default=None)
    if reviewer is None:
        state = "missing" if row["review_policy"] == "required" else "not_required"
        return {"state": state, "assignment_id": None}
    assignment = reviewer["id"]
    # review_state only uses the actor/role on builders and these result fields on the reviewer.
    builders = [cast(Assignment, item) for item in row["assignments"] if item["role"] == "builder"]
    completed = {"status": "completed"}
    candidate = cast(Assignment, {**reviewer, "status": "passed", "result": completed})
    seqs = (assigned, row["builder_finished_seq"], row["latest_verification_seq"])
    trees = row["review_trees"]
    start, end = trees.get(assignment, {}).get("start"), trees.get(assignment, {}).get("end")
    if not review_state("required", row["lead"], [*builders, candidate], *seqs)["passing"]:
        state = "ineligible"
    elif reviewer["status"] == "active":
        state = "pending"
    elif reviewer["status"] != "passed" or reviewer["result_status"] != "completed" or not start or not end:
        state = "incomplete"
    elif start[2:] != ("snapshot",) or end[2:] != ("snapshot",):
        state = "live"
    elif start != end:
        state = "changed"
    else:
        if any(record is None for _, _, record in records):
            return {"state": "incomplete", "assignment_id": assignment}
        wanted = {argv_identity(command) for command in row["checks"]}
        # The full gate uses either all selected check records or none. Require the
        # snapshot to match the latest applicable recorded tree in either case.
        ids = {item["id"] for _, identity, item in records if item is not None and (not wanted or identity in wanted)}
        verified_tree = next((tree for refs, tree in reversed(row["verification_events"]) if ids.intersection(refs)), None)
        if not ids or verified_tree is None:
            # A snapshot review without a recorded lead verification cannot satisfy the gate.
            return {"state": "unverified", "assignment_id": assignment}
        state = ("recorded_snapshot" if
                 review_state("required", row["lead"], [*builders, candidate], *seqs, trees, verified_tree)["passing"]
                 else "changed")
    return {"state": state, "assignment_id": assignment}


def _project(row: dict[str, Any], head: str | None) -> TaskProjection:
    truncated: list[str] = []
    reasons: list[str] = []
    records = [(item["seq"], item["identity"], _record(item)) for item in row["verifications"]]
    checks = _checks(row, records, head, reasons, truncated)
    assignments = []
    activity = [row["updated_at"], row["created_at"], row["last_event_at"]]
    activity.extend(check["recorded_at"] for check in checks)
    for item in row["assignments"]:
        activity.extend((item["started_at"], item["ended_at"], item["heartbeat_at"]))
    for index, item in enumerate(row["assignments"][-ASSIGNMENT_LIMIT:]):
        summary = item["result_summary"]
        assignments.append(AssignmentProjection(
            id=item["id"], role=item["role"], actor=_bound(item["actor"], 200, f"assignments.{index}.actor", truncated),
            status="completed" if item["status"] == "passed" else item["status"], ack_state=item["ack_state"],
            started_at=item["started_at"], ended_at=item["ended_at"], result_status=item["result_status"],
            summary=_bound(" ".join(summary.splitlines()), 200, f"assignments.{index}.summary", truncated)
            if isinstance(summary, str) else None,
        ))
    counts = dict.fromkeys(FINDING_SEVERITIES, 0)
    for finding in row["findings"]:
        counts[finding["severity"]] = finding["total"]
    return TaskProjection(
        view_schema=VIEW_SCHEMA, hipson_version=__version__, gate="not_evaluated", gate_note=GATE_NOTE,
        id=row["id"], status=row["status"],
        project=_bound(row["project"], 200, "project", truncated), lead=_bound(row["lead"], 200, "lead", truncated),
        title=_bound(row["title"], 160, "title", truncated), objective=_bound(row["objective"], 400, "objective", truncated),
        review_policy=row["review_policy"], checks=checks, freshness_source="recorded",
        freshness_note="Check freshness uses recorded evidence and HEAD. Working-tree content was not re-checked.",
        review=_review(row, records), assignments=assignments, omitted_assignments=len(row["assignments"]) - len(assignments),
        open_findings=counts,
        blocking_findings=[item["id"] for item in row["findings"] if item["severity"] == "blocking"],
        major_findings=[item["id"] for item in row["findings"] if item["severity"] == "major"],
        reasons=[_bound(reason, 240, f"reasons.{index}", truncated)
                 for index, reason in enumerate(reasons[:REASON_LIMIT])],
        omitted_reasons=max(0, len(reasons) - REASON_LIMIT), truncated_fields=truncated,
        created_at=row["created_at"], updated_at=row["updated_at"],
        last_activity_at=max((item for item in activity if _timestamp(item) is not None),
                             key=lambda item: _timestamp(item) or datetime.min.replace(tzinfo=UTC),
                             default=row["updated_at"]),
    )


def _project_rows(rows: Sequence[dict[str, Any]]) -> list[TaskProjection]:
    heads = _observe(list(dict.fromkeys(row["project"] for row in rows)), _head)
    return [_project(row, heads.get(row["project"])) for row in rows]


def project_task(store: Ledger, task: str) -> TaskProjection:
    result, = _project_rows(store.projection_rows(task=task, include_terminal=True))
    return result


def show_task(store: Ledger, task: str, *, cli_db: str | None = None) -> dict[str, Any]:
    return {**project_task(store, task),
            "evidence_policy": EVIDENCE_POLICY, "untrusted_fields": list(TASK_UNTRUSTED_FIELDS),
            "more": [_section_call(task, section, cli_db=cli_db) for section in SECTIONS]}


def _section_call(task: str, section: str, *, cli_db: str | None = None) -> ReadCall:
    return _read_call("task.show", {"task": task, "section": section}, cli_db)


def _section_prefix(value: Any, budget: int) -> Any:
    """Shorten a string or take a collection prefix within its serialized budget."""
    if isinstance(value, str):
        if budget < _size("…"):
            return ""
        low, high = 0, min(len(value) - 1, budget)
        while low < high:
            middle = (low + high + 1) // 2
            if _size(value[:middle] + "…") <= budget:
                low = middle
            else:
                high = middle - 1
        return value[:low] + "…"

    entries = value.items() if isinstance(value, dict) else enumerate(value)
    prefix: list[tuple[str | int, Any]] = []
    remaining = budget - 2  # Brackets or braces.
    for key, item in entries:
        overhead = bool(prefix) + (_size(key) + 1 if isinstance(key, str) else 0)
        available = remaining - overhead
        size = _size(item)
        if size > available:
            if available >= 2 and isinstance(item, (str, list, dict)):
                prefix.append((key, _section_prefix(item, available)))
            break
        prefix.append((key, item))
        remaining -= overhead + size
    return dict(prefix) if isinstance(value, dict) else [item for _, item in prefix]


def _fit_section_record(page: dict[str, Any], record: dict[str, Any]) -> None:
    # Ledger record keys and scalars are small. Cut the largest variable fields
    # first, retaining the record's top-level fields and the following cursor.
    fields = sorted(((_size(value), key) for key, value in record.items()
                     if isinstance(value, (str, list, dict)) and value), reverse=True)
    truncated: list[str] = []
    record["truncated_fields"] = truncated
    for size, key in fields:
        truncated.append(key)
        budget = max(2, SECTION_BUDGET - (_size(page) - size))
        prefix = _section_prefix(record[key], budget)
        if prefix == record[key]:
            truncated.pop()
            continue
        record[key] = prefix
        if key == "files" and "files_total" in record:
            record["files_truncated"] = len(prefix) < record["files_total"]
        if _size(page) <= SECTION_BUDGET:
            break


def _section_verification(row: sqlite3.Row) -> dict[str, Any]:
    try:
        evidence = json.loads(row["evidence"])
    except (TypeError, ValueError, RecursionError):
        evidence = None
    if not isinstance(evidence, dict):
        return {"seq": row["seq"], "invalid_evidence": True}
    files = evidence.get("files")
    files_total = len(files) if isinstance(files, list) else 0
    evidence["files_total"] = files_total
    evidence["files_truncated"] = files_total > SECTION_FILE_LIMIT
    if isinstance(files, list) and files_total > SECTION_FILE_LIMIT:
        evidence["files"] = files[:SECTION_FILE_LIMIT]
    return evidence


def read_section(store: Ledger, task: str, section: str, *, limit: int | None = None,
                 cursor: int | None = None) -> dict[str, Any]:
    """Page stored records within the byte and count caps, in ledger order."""
    queries: dict[str, tuple[int, str, Callable[[sqlite3.Row], Mapping[str, Any]]]] = {
        "verification": (6, "SELECT seq, evidence FROM verifications WHERE task = ? ORDER BY seq LIMIT ? OFFSET ?",
                         _section_verification),
        "assignments": (1, "SELECT * FROM assignments WHERE task = ? ORDER BY started_at, id LIMIT ? OFFSET ?",
                        store._assignment_dict),
        "findings": (3, "SELECT * FROM findings WHERE task = ? ORDER BY created_at, id LIMIT ? OFFSET ?", dict),
        "events": (1, "SELECT * FROM events WHERE task = ? ORDER BY seq LIMIT ? OFFSET ?", store._event_dict),
    }
    if section not in queries:
        raise ValueError(f"section must be one of {SECTIONS}")
    limit = SECTION_LIMIT if limit is None else limit
    cursor = 0 if cursor is None else cursor
    _validate_page(cursor, limit, position="cursor")
    min_version, sql, decode = queries[section]
    records: list[dict[str, Any]] = []
    page: dict[str, Any] = {"task": task, "section": section, "records": records, "next_cursor": None,
                            "evidence_policy": EVIDENCE_POLICY,
                            "untrusted_fields": [f"records[].{field}" for field in SECTION_UNTRUSTED_FIELDS[section]]}
    if section == "verification":
        page["files_note"] = "Read the full file inventory with task show --detail full."
    with store._transaction(write=False):
        store._task(task)
        version = store.connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()[0]
        if version < min_version:
            page["unavailable"] = f"schema v{version} does not store {section}"
            return page
        rows = store.connection.execute(sql, (task, limit + 1, cursor))
        row = next(rows, None)
        while row is not None and len(records) < limit:
            records.append(redact_value(dict(decode(row))))
            row = next(rows, None)
            position = cursor + len(records)
            page["next_cursor"] = position if row is not None else None
            if _size(page) > SECTION_BUDGET:
                if len(records) == 1:
                    _fit_section_record(page, records[0])
                else:
                    records.pop()
                    page["next_cursor"] = position - 1
                break
    return page


def _now_priority(row: dict[str, Any]) -> int:
    if row["has_unacknowledged"]:
        return 0
    if row["status"] in TERMINAL:
        return 5
    if row["status"] == "blocked" or any(item["severity"] in ("blocking", "major") for item in row["findings"]):
        return 1
    if row["has_active"]:
        return 2
    return 3 if row["status"] == "review" else 4


def _fit(tasks: list[TaskProjection], kind: ListKind, budget: int, offset: int, remaining: int,
         *, filters: dict[str, str | int | bool], cli_db: str | None,
         next_actions: Sequence[NextAction] = (), next_actions_omitted: int = 0) -> ListProjection:
    untrusted_fields = [f"tasks[].{field}" for field in TASK_UNTRUSTED_FIELDS]
    actions = list(next_actions)

    def envelope(count: int, *, stub: bool = False) -> ListProjection:
        omitted = remaining - count
        page = ListProjection(view_schema=VIEW_SCHEMA, hipson_version=__version__, gate="not_evaluated", gate_note=GATE_NOTE,
                              evidence_policy=EVIDENCE_POLICY, untrusted_fields=untrusted_fields,
                              kind=kind, tasks=[], truncated=omitted > 0 or stub, omitted=omitted,
                              more=_read_call(kind, {"detail": "compact", "offset": offset + count, **filters},
                                              cli_db) if omitted else None)
        if kind == "task.now":
            page["next_actions"] = actions
            page["next_actions_omitted"] = next_actions_omitted
        return page

    if tasks:
        first = tasks[0]
        stub = TaskStub(id=first["id"], status=first["status"], title="", too_large=True,
                        hint=_read_call("task.show", {"task": first["id"], "detail": "compact"}, cli_db)["cli"])
        minimum = envelope(1, stub=True)
        minimum["tasks"] = [stub]
    else:
        minimum = envelope(0)
    # Fit the ranked prefix before task rows, leaving room for page framing
    # and a task stub so paging can always advance.
    while actions and _size(minimum) > budget:
        actions.pop()
        next_actions_omitted += 1
        minimum["next_actions_omitted"] = next_actions_omitted

    view = envelope(0)
    count = 0
    used = 0
    for task in tasks:
        candidate = envelope(count + 1)
        next_size = used + bool(count) + _size(task)
        if _size(candidate) + next_size > budget:
            break
        view, used = candidate, next_size
        count += 1
    if count:
        view["tasks"] = tasks[:count]
    elif tasks:
        view = minimum
        stub["title"] = tasks[0]["title"]
        while stub["title"] and _size(view) > budget:
            stub["title"] = stub["title"][:len(stub["title"]) // 2]
    if _size(view) > budget:
        raise ValueError("Byte budget is too small for the projection envelope or task stub")
    return view


def _validate_page(value: int, limit: int | None, *, position: str) -> None:
    if not 0 <= value <= MAX_CURSOR:
        raise ValueError(f"{position} must be between 0 and {MAX_CURSOR}")
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")


def _list_page(store: Ledger | None, rows: list[dict[str, Any]], *, kind: ListKind, project: str | None,
               include_terminal: bool, byte_budget: int | None = None, offset: int = 0,
               limit: int | None = None, cli_db: str | None = None, text: bool = False) -> ListProjection:
    """Project tasks in board creation order or now attention order."""
    filters: dict[str, str | int | bool] = {}
    if limit is not None:
        filters["limit"] = limit
    if project is not None:
        filters["project"] = project
    if include_terminal:
        filters["all"] = True
    end = None if limit is None else offset + limit
    actions: list[NextAction] = []
    omitted = 0
    if kind == "task.now":
        from hipson import next_actions

        rows.sort(key=lambda row: (_now_priority(row), row["id"]))
        projected = _project_rows(rows)
        actions, omitted = next_actions.build(rows, projected, cli_db=cli_db)
        tasks = projected[offset:end]
    else:
        tasks = _project_rows(rows[offset:end])
    page = _fit(tasks, kind, LIST_BUDGETS[kind] if byte_budget is None else byte_budget,
                offset, max(0, len(rows) - offset), filters=filters, cli_db=cli_db,
                next_actions=actions, next_actions_omitted=omitted)
    if kind == "task.now" and store is not None:
        # Reserve room for the activity read and its follow-up before using
        # the shorter acknowledgment wherever the result fits on this page.
        next_actions.acknowledge_visible_results(store, page, text=text)
    return page


def project_list(store: Ledger | None, *, kind: ListKind, project: str | None = None, include_terminal: bool = False,
                 byte_budget: int | None = None, offset: int = 0, limit: int | None = None,
                 cli_db: str | None = None, text: bool = False) -> ListProjection:
    _validate_page(offset, limit, position="offset")
    rows = store.projection_rows(project=project, include_terminal=include_terminal) if store else []
    return _list_page(store, rows, kind=kind, project=project, include_terminal=include_terminal,
                      byte_budget=byte_budget, offset=offset, limit=limit, cli_db=cli_db, text=text)


def resume_inputs(store: Ledger | None, *, project: str | None, include_terminal: bool,
                  cli_db: str | None = None, text: bool = False) -> tuple[ListProjection, list[dict[str, Any]]]:
    rows = store.projection_rows(project=project, include_terminal=include_terminal) if store else []
    return _list_page(store, rows, kind="task.now", project=project, include_terminal=include_terminal,
                      cli_db=cli_db, text=text), rows
