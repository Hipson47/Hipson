"""Transactional task ledger written by the lead on behalf of workers.

Actor names are recorded labels, not authenticated identities. Path claims are
lexical coordination records; they do not enforce filesystem access.
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
import re
import shlex
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, TypedDict, cast

from hipson.home import detect_hipson_home
from hipson.redaction import redact_text, redact_value
from hipson.task_calls import ReadCall, gate_fix_calls, read_call, task_lookup_calls

SCHEMA_VERSION = 12
BUSY_TIMEOUT_MS = 10_000
SQLITE_MAX_INTEGER = 2**63 - 1
STATUSES = ("open", "in_progress", "blocked", "review", "done", "cancelled")
ROLES = ("builder", "reviewer", "explorer", "researcher")
MODES = ("ro", "rw")
# 'required': done needs an independent, read-only review of the verified tree (see review_state).
REVIEW_POLICIES = ("none", "required")
RESULTS = ("passed", "failed", "cancelled")
FINDING_SEVERITIES = ("blocking", "major", "minor", "info")
FINDING_STATUSES = ("open", "fixed", "wontfix", "duplicate")

# Kept here so installed CLI/MCP builds do not depend on a repository checkout.
# Tests require exact parity with schemas/task-result.schema.json.
TASK_RESULT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Hipson Worker Result",
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "files_changed", "commands", "findings", "risks"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "blocked"]},
        "summary": {"type": "string"},
        "files_changed": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["path", "change"],
                "properties": {"path": {"type": "string"}, "change": {"type": "string"}},
            },
        },
        "commands": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["command", "exit_code"],
                "properties": {"command": {"type": "string"}, "exit_code": {"type": ["integer", "null"]}},
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["severity", "title", "detail", "location"],
                "properties": {
                    "severity": {"type": "string", "enum": list(FINDING_SEVERITIES)},
                    "title": {"type": "string", "pattern": r"\S"},
                    "detail": {"type": "string"},
                    "location": {"type": ["string", "null"]},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}
TERMINAL_STATUSES = frozenset({"done", "cancelled"})
TRANSITIONS = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"blocked", "review", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "review": {"in_progress", "done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}

# Execute statements individually: executescript would implicitly commit an
# existing transaction. Initialization, including the version, must be atomic.
SCHEMA: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_version (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL
    )""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        acceptance TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'blocked', 'review', 'done', 'cancelled')),
        lead TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 0),
        checks TEXT NOT NULL DEFAULT '[]',
        needs TEXT NOT NULL DEFAULT '[]',
        review_policy TEXT NOT NULL DEFAULT 'none' CHECK (review_policy IN ('none', 'required'))
    )""",
    """CREATE TABLE assignments (
        id TEXT PRIMARY KEY,
        task TEXT NOT NULL REFERENCES tasks(id),
        actor TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('builder', 'reviewer', 'explorer', 'researcher')),
        mode TEXT NOT NULL CHECK (mode IN ('ro', 'rw')),
        scope TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'passed', 'failed', 'cancelled')),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        owner_pid INTEGER CHECK (owner_pid BETWEEN 1 AND 2147483647),
        owner_boot_id TEXT,
        owner_host TEXT,
        lease_until TEXT,
        result TEXT,
        owner_pid_namespace TEXT,
        activity TEXT,
        failure_journal TEXT,
        acknowledged_at TEXT,
        ack_note TEXT NOT NULL DEFAULT '',
        changes TEXT,
        environment TEXT,
        needs TEXT,
        usage TEXT,
        CHECK ((status = 'active' AND ended_at IS NULL) OR (status <> 'active' AND ended_at IS NOT NULL))
    )""",
    """CREATE TABLE claims (
        assignment TEXT NOT NULL REFERENCES assignments(id),
        path TEXT NOT NULL,
        PRIMARY KEY (assignment, path)
    )""",
    """CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL REFERENCES tasks(id),
        actor TEXT NOT NULL,
        type TEXT NOT NULL,
        summary TEXT NOT NULL,
        refs TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )""",
    "CREATE INDEX idx_tasks_project_status ON tasks(project, status)",
    "CREATE INDEX idx_assignments_task_status ON assignments(task, status)",
    "CREATE INDEX idx_events_task_seq ON events(task, seq)",
    """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'task events are append-only'); END""",
    """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
        BEGIN SELECT RAISE(ABORT, 'task events are append-only'); END""",
)

MIGRATION_V1_TO_V2 = (
    "ALTER TABLE assignments ADD COLUMN owner_pid INTEGER CHECK (owner_pid BETWEEN 1 AND 2147483647)",
    "ALTER TABLE assignments ADD COLUMN owner_boot_id TEXT",
    "ALTER TABLE assignments ADD COLUMN owner_host TEXT",
    "ALTER TABLE assignments ADD COLUMN lease_until TEXT",
    "UPDATE schema_version SET version = 2 WHERE singleton = 1",
)

FINDINGS_DDL = (
    """CREATE TABLE findings (
        id TEXT PRIMARY KEY,
        task TEXT NOT NULL REFERENCES tasks(id),
        source_assignment TEXT REFERENCES assignments(id),
        severity TEXT NOT NULL CHECK (severity IN ('blocking', 'major', 'minor', 'info')),
        title TEXT NOT NULL,
        detail TEXT NOT NULL,
        location TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('open', 'fixed', 'wontfix', 'duplicate')),
        resolved_by_assignment TEXT REFERENCES assignments(id),
        resolution_note TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_at TEXT,
        CHECK ((status = 'open' AND resolved_at IS NULL AND resolved_by_assignment IS NULL)
            OR (status <> 'open' AND resolved_at IS NOT NULL))
    )""",
    "CREATE INDEX idx_findings_task_status_severity ON findings(task, status, severity)",
)
SCHEMA = (*SCHEMA, *FINDINGS_DDL)
MIGRATION_V2_TO_V3 = (
    "ALTER TABLE assignments ADD COLUMN result TEXT",
    *FINDINGS_DDL,
    "UPDATE schema_version SET version = 3 WHERE singleton = 1",
)
MIGRATION_V3_TO_V4 = (
    "ALTER TABLE assignments ADD COLUMN owner_pid_namespace TEXT",
    "UPDATE schema_version SET version = 4 WHERE singleton = 1",
)
LEGACY_ACK_NOTE = 'legacy: finished before v5 acknowledgement tracking'
MIGRATION_V4_TO_V5 = (
    "ALTER TABLE assignments ADD COLUMN activity TEXT",
    "ALTER TABLE assignments ADD COLUMN failure_journal TEXT",
    "ALTER TABLE assignments ADD COLUMN acknowledged_at TEXT",
    "ALTER TABLE assignments ADD COLUMN ack_note TEXT NOT NULL DEFAULT ''",
    "UPDATE assignments SET ack_note = 'legacy: finished before v5 acknowledgement tracking' WHERE status <> 'active'",
    "UPDATE schema_version SET version = 5 WHERE singleton = 1",
)
VERIFICATIONS_DDL = (
    """CREATE TABLE verifications (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        task TEXT NOT NULL REFERENCES tasks(id),
        evidence TEXT NOT NULL
    )""",
    "CREATE INDEX idx_verifications_task_seq ON verifications(task, seq)",
    """CREATE TRIGGER verifications_no_update BEFORE UPDATE ON verifications
        BEGIN SELECT RAISE(ABORT, 'verifications are append-only'); END""",
    """CREATE TRIGGER verifications_no_delete BEFORE DELETE ON verifications
        BEGIN SELECT RAISE(ABORT, 'verifications are append-only'); END""",
)
SCHEMA = (*SCHEMA, *VERIFICATIONS_DDL)
MIGRATION_V5_TO_V6 = (*VERIFICATIONS_DDL, "UPDATE schema_version SET version = 6 WHERE singleton = 1")
MIGRATION_V6_TO_V7 = (
    "ALTER TABLE tasks ADD COLUMN checks TEXT NOT NULL DEFAULT '[]'",
    "UPDATE schema_version SET version = 7 WHERE singleton = 1",
)
MIGRATION_V7_TO_V8 = (
    "ALTER TABLE assignments ADD COLUMN changes TEXT",
    "UPDATE schema_version SET version = 8 WHERE singleton = 1",
)
MIGRATION_V8_TO_V9 = (
    "ALTER TABLE assignments ADD COLUMN environment TEXT",
    "UPDATE schema_version SET version = 9 WHERE singleton = 1",
)
MIGRATION_V9_TO_V10 = (
    "ALTER TABLE tasks ADD COLUMN needs TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE assignments ADD COLUMN needs TEXT",
    "UPDATE schema_version SET version = 10 WHERE singleton = 1",
)
MIGRATION_V10_TO_V11 = (
    "ALTER TABLE tasks ADD COLUMN review_policy TEXT NOT NULL DEFAULT 'none'"
    " CHECK (review_policy IN ('none', 'required'))",
    "UPDATE schema_version SET version = 11 WHERE singleton = 1",
)
MIGRATION_V11_TO_V12 = (
    "ALTER TABLE assignments ADD COLUMN usage TEXT",
    "UPDATE schema_version SET version = 12 WHERE singleton = 1",
)
MIGRATIONS = (
    MIGRATION_V1_TO_V2, MIGRATION_V2_TO_V3, MIGRATION_V3_TO_V4, MIGRATION_V4_TO_V5, MIGRATION_V5_TO_V6,
    MIGRATION_V6_TO_V7, MIGRATION_V7_TO_V8, MIGRATION_V8_TO_V9, MIGRATION_V9_TO_V10, MIGRATION_V10_TO_V11,
    MIGRATION_V11_TO_V12,
)
# One version per migration, plus the current one. Derived so the Ledger guard and `hipson init`
# cannot drift apart again, and listing SCHEMA_VERSION separately keeps the refusal fail-closed:
# bumping it without adding a migration leaves the previous version unsupported and loud, rather
# than accepting a store this build would silently never migrate.
SUPPORTED_SCHEMA_VERSIONS = (*range(1, len(MIGRATIONS) + 1), SCHEMA_VERSION)
MAX_NEEDS = 16
NEED_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_CHECKS = 16
MAX_CHECK_ARGS = 64
CHECK_ARG_LIMIT = 4096


class Task(TypedDict):
    artifact_kind: str
    schema_version: str
    id: str
    project: str
    title: str
    objective: str
    acceptance: list[str]
    checks: list[list[str]]
    needs: list[str]
    review_policy: str
    status: str
    lead: str
    created_at: str
    updated_at: str
    revision: int


class FileChange(TypedDict):
    path: str
    change: str


class ResultCommand(TypedDict):
    command: str
    exit_code: int | None


class ResultFinding(TypedDict):
    severity: str
    title: str
    detail: str
    location: str | None


class TaskResult(TypedDict):
    status: str
    summary: str
    files_changed: list[FileChange]
    commands: list[ResultCommand]
    findings: list[ResultFinding]
    risks: list[str]


class Finding(TypedDict):
    id: str
    task: str
    source_assignment: str | None
    severity: str
    title: str
    detail: str
    location: str
    status: str
    resolved_by_assignment: str | None
    resolution_note: str
    created_at: str
    updated_at: str
    resolved_at: str | None


class RuntimeUsage(TypedDict):
    source: str | None
    tokens: dict[str, int | None] | None
    reason: str | None


class AssignmentUsage(RuntimeUsage):
    elapsed_seconds: float


class Assignment(TypedDict):
    id: str
    task: str
    actor: str
    role: str
    mode: str
    scope: list[str]
    status: str
    started_at: str
    ended_at: str | None
    owner_pid: int | None
    owner_boot_id: str | None
    owner_host: str | None
    owner_pid_namespace: str | None
    lease_until: str | None
    result: TaskResult | None
    activity: dict[str, Any] | None
    failure_journal: dict[str, Any] | None
    acknowledged_at: str | None
    ack_note: str
    ack_state: str
    changes: dict[str, Any] | None
    environment: dict[str, Any] | None
    needs: list[str] | None
    usage: AssignmentUsage | None


class ReapedAssignment(TypedDict):
    assignment: Assignment
    reason: str


class Claim(TypedDict):
    assignment: str
    path: str


class TaskView(Task):
    assignments: list[Assignment]
    claims: list[Claim]
    findings: list[Finding]
    verification: dict[str, Any]
    review: dict[str, Any]


class TaskEvent(TypedDict):
    artifact_kind: str
    schema_version: str
    seq: int
    task: str
    actor: str
    type: str
    summary: str
    refs: list[str]
    timestamp: str


class LedgerError(ValueError):
    """Invalid ledger operation, suitable for a CLI error message."""

    def __init__(self, message: str, *, code: str | None = None, fix: list[ReadCall] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.fix = [] if fix is None else fix

    def error_payload(self) -> dict[str, Any] | None:
        if self.code is None:
            return None
        return {"error_schema": 1, "code": self.code, "message": refusal_text(self),
                "fix": redact_value(self.fix)}


class LedgerOpenError(LedgerError, sqlite3.OperationalError):
    """A database was unavailable before SQLite opened the read connection."""


class GateRefusedError(LedgerError):
    """A gate can refuse on several reasons, in the order reported to the caller."""

    def __init__(self, message: str, *, code: str | None = None, reason_codes: Sequence[str] = (),
                 fix: list[ReadCall] | None = None) -> None:
        super().__init__(message, code=reason_codes[0] if reason_codes else code, fix=fix)
        self.reason_codes = list(reason_codes)


class CloseRefusedError(GateRefusedError):
    """A close was refused before any transition."""

    def __init__(self, task: str, reasons: list[str], *, code: str | None = None,
                 reason_codes: Sequence[str] = (), fix: list[ReadCall] | None = None) -> None:
        if code is not None and not reason_codes:
            reason_codes = [code] * len(reasons)
        super().__init__(f"Cannot close {task}: " + "; ".join(reasons), code=code, reason_codes=reason_codes, fix=fix)
        self.reasons = reasons


def refusal_text(value: object) -> str:
    from hipson.task_activity import READ_LIMIT, safe_text

    text = str(value)
    for char in ("\x00", "\ufeff", "\ufffe"):
        text = text.replace(char, "")
    return safe_text(text[:READ_LIMIT], READ_LIMIT)


def print_error(error: BaseException, message: str, *, json_output: bool) -> None:
    payload = error.error_payload() if isinstance(error, LedgerError) else None
    print_refusal(payload, message, json_output=json_output)


def print_refusal(payload: dict[str, Any] | None, message: str, *, json_output: bool) -> None:
    if json_output and payload is not None:
        print(json.dumps(payload, ensure_ascii=True), file=sys.stderr, flush=True)
        return
    print(refusal_text(message), file=sys.stderr, flush=True)
    if payload is not None:
        for call in payload["fix"]:
            print(f"fix: {call['cli']}", file=sys.stderr, flush=True)


RESERVED_EVENT_TYPES = frozenset({
    "opened", "assigned", "status", "finished", "reaped", "acknowledged", "lead_verified", "checks_set",
    "claim_conflict", "reopened", "override", "observed", "routed", "checkpointed", "restored",
    "review_policy", "flow", "reviewed",
})
REVIEWED_PATTERN = re.compile(r": (start|end) revision=(\S+); fingerprint=(\S+?)(?:; source=(live|snapshot))?$")
VERIFIED_PATTERN = re.compile(r"; revision=(\S+); fingerprint=(\S+); changed=")


def reserved_event_type(type: str) -> bool:
    """Lifecycle readers trust these types; free-form events must not forge them."""
    return (type in RESERVED_EVENT_TYPES or type.startswith("finding_")
            or (type.endswith("_override") and type != "project_mismatch_override"))


def review_state(policy: str, lead: str, assignments: Sequence[Assignment], assigned_seq: dict[str, int],
                 builder_seq: int, verified_seq: int, trees: dict[str, dict[str, tuple[str, ...]]] | None = None,
                 verified_tree: tuple[str, str] | None = None) -> dict[str, Any]:
    """Decide whether a required independent review covers the current candidate.

    A review qualifies when a read-only reviewer, whose actor label differs from the lead and every builder,
    passed with a completed structured result, was assigned after both the latest finished builder and the
    latest accepted lead verification, and saw the verified tree: `task run` gives a reviewer an immutable snapshot
    of the candidate (review_snapshot) and records its revision and fingerprint, re-hashed from the snapshot, when
    the reviewer starts and ends (`reviewed` events, source=snapshot); both must equal the accepted verification's.
    A review of the live working tree (source=live, or older events without a source) never qualifies: equal
    fingerprints at its start and end do not prove what it read in between. `trees=None` checks ordering only
    (pure contracts); the ledger view always passes them.
    Labels are not authenticated: this is a coordination guarantee, not a security boundary.
    """
    if policy != "required":
        return {"required": False, "passing": True, "accepted": None, "current": None, "reasons": []}
    others = {lead.casefold(), *(item["actor"].casefold() for item in assignments if item["role"] == "builder")}

    def problem(item: Assignment) -> str | None:
        seq = assigned_seq.get(item["id"], 0)
        if seq <= builder_seq:
            return "it started before the latest builder finished"
        if seq <= verified_seq:
            return "it started before the accepted lead verification, so it did not review the verified tree"
        if item["mode"] != "ro":
            return "it is not read-only"
        if item["actor"].casefold() in others:
            return f"actor {item['actor']} is the lead or a builder of this task"
        if item["status"] != "passed":
            return f"it is {item['status']}"
        if item["result"] is None or item["result"]["status"] != "completed":
            return "it has no completed structured result (task run --result-file)"
        if trees is not None:
            tree = trees.get(item["id"], {})
            if "start" not in tree or "end" not in tree:
                return "it has no recorded reviewed tree (run reviewers through hipson task run)"
            if tree["start"][2:] != ("snapshot",) or tree["end"][2:] != ("snapshot",):
                return ("it read the live working tree, not an immutable snapshot of the verified candidate "
                        "(run reviewers through hipson task run without --live-tree, or hipson task flow)")
            if tree["start"] != tree["end"]:
                return "the reviewed snapshot changed during the review"
            if verified_tree is not None and tree["start"][:2] != verified_tree:
                return "it reviewed a different tree than the accepted lead verification"
        return None

    reviews = sorted((item for item in assignments if item["role"] == "reviewer"),
                     key=lambda item: assigned_seq.get(item["id"], 0))
    accepted = next((item["id"] for item in reversed(reviews) if problem(item) is None), None)
    # The latest review assigned after the latest builder and accepted verification, whatever its outcome.
    current = next((item["id"] for item in reversed(reviews)
                    if assigned_seq.get(item["id"], 0) > max(builder_seq, verified_seq)), None)
    # A later review that ran and did not complete is negative evidence: an earlier pass does not outvote it, and
    # a newer cancelled (interrupted) one does not hide it.
    cutoff = assigned_seq.get(accepted, 0) if accepted is not None else 0
    failed = [item for item in reviews if assigned_seq.get(item["id"], 0) > cutoff and (
        item["status"] == "failed" or (item["status"] == "passed" and (
            item["result"] is None or item["result"]["status"] != "completed")))]
    if accepted is not None and failed:
        return {"required": True, "passing": False, "accepted": None, "current": current, "reasons": [
            f"review required: later review {failed[-1]['id']} did not complete ({problem(failed[-1])}) after "
            f"accepted review {accepted}; run the reviewer again"]}
    if accepted is not None:
        return {"required": True, "passing": True, "accepted": accepted, "current": current, "reasons": []}
    reason = ("review required: no reviewer assignment" if not reviews else
              f"review required: latest review {reviews[-1]['id']} does not qualify: {problem(reviews[-1])}")
    return {"required": True, "passing": False, "accepted": None, "current": current,
            "reasons": [reason + "; run an independent read-only reviewer after the latest builder and lead "
                                 "verification (hipson task flow, or hipson task run --role reviewer --mode ro)"]}


def done_blockers(view: TaskView) -> list[str]:
    """Done blockers other than verification, each with the command that clears it."""
    task = view["id"]
    active = [item for item in view["assignments"] if item["status"] == "active"]
    blockers = [f"active assignment {item['id']}: wait for it, or hipson task reap --task {task}" for item in active]
    blockers += [f"unacknowledged result {item['id']}: read it (hipson task show --task {task}), then "
                 f"hipson task ack --assignment {item['id']}"
                 for item in view["assignments"] if item["ack_state"] == "unacknowledged"]
    blockers += [f"open {item['severity']} finding {item['id']}: fix it, then hipson task finding update "
                 f"--finding {item['id']} --to fixed --actor {view['lead']}"
                 for item in view["findings"] if item["severity"] in ("blocking", "major")]
    if view["claims"] and not active:
        blockers.append(f"held claims: hipson task reap --task {task}")
    review = view.get("review")
    if review is not None and not review["passing"]:
        blockers += review["reasons"]
    return blockers


# Classify existing gate reasons at the error boundary. Declared checks keep their raw reasons
# in verification["checks"], so command text never participates in matching these prefixes.
_GATE_REASON_CODES = {
    "missing lead verification": "verify.lead_missing",
    "missing:": "close.checks_missing",
    "failing verification": "close.checks_failed",
    "stale: HEAD moved": "verify.head_moved",
    "stale: working tree changed": "verify.tree_changed",
    "stale:": "close.checks_stale",
    "unavailable working-tree fingerprint: Project directory is unavailable": "project.unavailable",
    "unavailable working-tree fingerprint: HEAD moved while fingerprinting": "verify.head_moved",
    "unavailable working-tree fingerprint: Project changed while fingerprinting": "verify.tree_changed",
    "unavailable working-tree fingerprint: Git filter configuration": "verify.git_filters_unavailable",
    "unavailable working-tree fingerprint: Git repository is present": "verify.git_state_unavailable",
    "unavailable working-tree fingerprint: Git HEAD is unavailable": "verify.git_head_unavailable",
    "unavailable working-tree fingerprint: Git content inventory": "verify.git_inventory_unavailable",
    "unavailable working-tree fingerprint: Git submodules": "verify.git_submodules",
    "unavailable working-tree fingerprint: Invalid repository path": "verify.inventory_path_invalid",
    "unavailable working-tree fingerprint: Directory symlink": "verify.directory_symlink",
    "unavailable working-tree fingerprint: Special file": "verify.special_file",
    "unavailable working-tree fingerprint: Project content exceeds": "verify.snapshot_budget",
    "unavailable working-tree fingerprint: A symlink points outside": "verify.external_symlink",
    "unavailable working-tree fingerprint: Git normalized content hash": "verify.git_hash_unavailable",
    "unavailable working-tree fingerprint": "verify.fingerprint_unavailable",
    "verification was not recorded": "verify.lead_required",
    "partial content coverage": "verify.coverage_partial",
    "verification process cleanup": "verify.cleanup_unconfirmed",
    "declared checks differ": "close.checks_mismatch",
    "active assignment": "close.assignment_active",
    "claim ": "close.claims_held",
    "held claims": "close.claims_held",
    "unacknowledged result": "close.results_unacknowledged",
    "open blocking finding": "close.findings_open",
    "open major finding": "close.findings_open",
    "review required": "close.review_required",
}


def _gate_raw_reasons(verification: dict[str, Any], reasons: list[str]) -> list[str]:
    raw = [reason for check in verification["checks"] for reason in check["reasons"]]
    # Raw check reasons replace their formatted copies; the gate's own reasons follow them.
    return raw + reasons[len(raw):]


def _gate_reason_codes(verification: dict[str, Any], reasons: list[str]) -> list[str]:
    return [_gate_reason_code(reason) for reason in _gate_raw_reasons(verification, reasons)]


def _gate_reason_code(reason: str) -> str:
    if (reason.startswith("unavailable working-tree fingerprint: Project ")
            and "is not a Git repository and is too large to fingerprint (" in reason):
        return "verify.walk_limit"
    return next((code for prefix, code in _GATE_REASON_CODES.items() if reason.startswith(prefix)), "close.refused")


def close_refusal(view: TaskView, reasons: list[str], db: Path, *, include_verification: bool = True) -> CloseRefusedError:
    verification = view["verification"] if include_verification else {"checks": []}
    codes = _gate_reason_codes(verification, reasons)
    return CloseRefusedError(view["id"], reasons, reason_codes=codes,
                             fix=gate_fix_calls(list(zip(_gate_raw_reasons(verification, reasons), codes, strict=True)), view, db))


class ClaimConflictError(LedgerError):
    """A conflicting assignment was refused and its conflict event committed."""


def default_ledger_db() -> Path:
    override = os.environ.get("HIPSON_LEDGER")
    return Path(override).expanduser().resolve() if override else detect_hipson_home()[0] / "ledger.sqlite"


def normalize_scope(scope: Sequence[str]) -> list[str]:
    """Canonical lexical repo paths, including '.' for the entire project."""
    normalized = []
    for raw in scope:
        value = raw.replace("\\", "/")
        path = PurePosixPath(value)
        if not value or "\x00" in value or path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
            raise LedgerError("Scope must contain relative repository paths without '..' or drive prefixes",
                code="scope.path_invalid")
        # Stripping would silently rename a path ('src ' -> 'src') and hide overlaps; refuse edge whitespace instead.
        if any(part != part.strip() for part in path.parts):
            raise LedgerError(f"Scope path components must not start or end with whitespace: {raw!r}",
                code="scope.path_whitespace")
        normalized.append(path.as_posix())
    return list(dict.fromkeys(normalized))


def _overlaps(left: str, right: str) -> bool:
    return left == "." or right == "." or left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _same_project(left: str, right: str) -> bool:
    if sys.platform in ("win32", "darwin"):
        return left.casefold() == right.casefold()
    return left == right


def _claim_key(path: str) -> str:
    """A claimed path as the default filesystem sees it: case-insensitive on Windows and macOS, and on Windows
    without the trailing dots and spaces it ignores ("src." is "src")."""
    if sys.platform == "win32" and path != ".":
        path = "/".join(part.rstrip(" .") for part in path.split("/"))
    return path.casefold() if sys.platform in ("win32", "darwin") else path


def _claims_overlap(left: str, right: str) -> bool:
    """Write claims conflict as the default filesystem sees paths."""
    return _overlaps(_claim_key(left), _claim_key(right))


def validate_assignment_scope(scope: Sequence[str], *, mode: str) -> list[str]:
    """Validate claims before opening a store or starting an assignment transaction."""
    paths = normalize_scope(scope)
    if mode == "rw" and not paths:
        # An rw assignment without claims would let two writers share a tree unnoticed.
        raise LedgerError("An rw assignment needs at least one scope path; use '.' for the whole project",
            code="scope.required")
    if mode == "rw" and any(redact_text(path) != path for path in paths):
        # Redacting a claim would silently change the path it protects.
        raise LedgerError("Write scope paths must not contain secret-shaped text", code="scope.path_sensitive")
    return [redact_text(path) for path in paths]


def validate_status_override(*, to: str, force: bool, reason: str) -> None:
    if type(force) is not bool or not isinstance(reason, str):
        raise LedgerError("force must be boolean and reason must be text", code="status.override_type")
    if force:
        require_text(reason, "Override reason")
        if to not in ("review", "done"):
            raise LedgerError("--force and --reason apply only to review or done", code="status.override_target")
    elif reason:
        raise LedgerError("--reason requires --force", code="status.override_reason_without_force")


def require_text(value: str, field: str) -> str:
    """Require nonblank text at both the ledger and transport boundaries."""
    if not value.strip():
        raise LedgerError(f"{field} must not be empty", code="input.text_required")
    if _has_surrogate(value):
        raise LedgerError(f"{field} must be valid Unicode text (lone surrogate)", code="input.text_invalid_unicode")
    return value.strip()


def _has_surrogate(value: str) -> bool:
    # Lone surrogates survive json.dumps(ensure_ascii=True) but break every UTF-8 writer later.
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def validate_checks(checks: Sequence[Sequence[str]]) -> list[list[str]]:
    """Acceptance checks are exact argv lists run by the lead without a shell."""
    if isinstance(checks, str) or len(checks) > MAX_CHECKS:
        raise LedgerError(f"Acceptance checks must be a list of at most {MAX_CHECKS} argv lists",
            code="checks.list_invalid")
    normalized: list[list[str]] = []
    for check in checks:
        if isinstance(check, str) or not isinstance(check, Sequence) or not check or len(check) > MAX_CHECK_ARGS:
            raise LedgerError(f"Each acceptance check must be a nonempty argv list of at most {MAX_CHECK_ARGS} items",
                code="checks.argv_invalid")
        argv = list(check)
        for arg in argv:
            if (not isinstance(arg, str) or len(arg) > CHECK_ARG_LIMIT
                    or any(ord(char) < 32 or ord(char) == 127 for char in arg)):
                raise LedgerError(f"Acceptance check arguments must be strings of at most {CHECK_ARG_LIMIT} "
                                  "characters without control characters", code="checks.argument_invalid")
            if redact_text(arg) != arg:
                raise LedgerError("Acceptance checks must not contain secret-shaped text; pass secrets via the environment",
                    code="checks.argument_sensitive")
        if not argv[0].strip():
            raise LedgerError("An acceptance check needs a command name", code="checks.command_required")
        if argv not in normalized:
            normalized.append(argv)
    return normalized


def checks_text(checks: Sequence[Sequence[str]]) -> str:
    return '; '.join(shlex.join(check) for check in checks) or 'none declared'


def validate_needs(needs: Sequence[str]) -> list[str]:
    """Capability tags a task or run needs (e.g. code_write, web_research): lower-case slugs, deduplicated."""
    if isinstance(needs, str) or len(needs) > MAX_NEEDS:
        raise LedgerError(f"Capability tags must be a list of at most {MAX_NEEDS} names",
            code="capability.tags_invalid")
    tags: list[str] = []
    for tag in needs:
        if not isinstance(tag, str) or not NEED_PATTERN.fullmatch(tag):
            raise LedgerError(f"Capability tags are lower-case names such as code_write, not {tag!r}",
                code="capability.tag_invalid")
        if tag not in tags:
            tags.append(tag)
    return tags


def _validate_result_value(value: Any, schema: dict[str, Any], path: str) -> None:
    """Validate the fixed result contract's keywords, without a runtime dependency."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not any(type(value) is types[name] for name in names):
        raise LedgerError(f"Invalid worker result at {path}: expected {' or '.join(names)}",
            code="result.type_invalid")
    if "enum" in schema and value not in schema["enum"]:
        raise LedgerError(f"Invalid worker result at {path}: expected one of {schema['enum']}",
            code="result.value_invalid")
    if isinstance(value, str) and "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise LedgerError(f"Invalid worker result at {path}: must not be empty or blank", code="result.text_blank")
    if isinstance(value, str) and _has_surrogate(value):
        raise LedgerError(f"Invalid worker result at {path}: lone surrogate is not valid Unicode text",
            code="result.text_invalid_unicode")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) - properties.keys():
            raise LedgerError(f"Invalid worker result at {path}: unknown properties", code="result.properties_unknown")
        for key in schema["required"]:
            if key not in value:
                raise LedgerError(f"Invalid worker result at {path}.{key}: required property missing",
                    code="result.property_required")
            _validate_result_value(value[key], properties[key], f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_result_value(item, schema["items"], f"{path}[{index}]")


def validate_task_result(value: object) -> TaskResult:
    """Reject malformed results and return a detached, redacted copy."""
    _validate_result_value(value, TASK_RESULT_SCHEMA, "result")

    def redact(item: Any) -> Any:
        if isinstance(item, str):
            return redact_text(item)
        if isinstance(item, list):
            return [redact(child) for child in item]
        if isinstance(item, dict):
            return {key: redact(child) for key, child in item.items()}
        return item

    return cast(TaskResult, redact(value))


def load_task_result(path: str | Path) -> TaskResult:
    """Validate before opening a ledger, including duplicate keys and non-JSON numbers."""
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LedgerError("Invalid worker result JSON: duplicate object property",
                    code="result.json_duplicate_property")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise LedgerError("Invalid worker result JSON: non-finite number", code="result.json_nonfinite")

    try:
        # Bound the allocation even if a worker grows/replaces the file while read.
        with Path(path).expanduser().open("rb") as stream:
            data = stream.read(2**20 + 1)
        if len(data) > 2**20:
            raise LedgerError("Invalid worker result JSON: exceeds 1 MiB limit", code="result.json_too_large")
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object, parse_constant=invalid_constant,
        )
        return validate_task_result(value)
    except LedgerError:
        raise
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Invalid worker result JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            code="result.json_invalid") from exc
    except UnicodeError as exc:
        raise LedgerError("Invalid worker result JSON: expected UTF-8", code="result.json_encoding") from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        raise LedgerError("Invalid worker result JSON: parser resource limit",
            code="result.json_resource_limit") from exc


def _changes(raw: str) -> dict[str, Any]:
    """Change observations recorded before incomplete_reasons existed still read and render."""
    record = json.loads(raw)
    if isinstance(record, dict):
        record.setdefault("incomplete_reasons", [])
    return record


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _assignment_usage(usage: RuntimeUsage, started: str, ended: str) -> AssignmentUsage:
    elapsed = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
    return {**usage, "elapsed_seconds": max(0.0, elapsed)}


def _boot_id() -> str | None:
    try:
        if sys.platform == "linux":
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() or None
        if sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True,
                text=True, encoding="ascii", timeout=2, check=False,
            )
            return (result.stdout.strip() or None) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    # Windows has no boot identity here: use leases or explicit finish to guard PID reuse.
    return None


def _pid_namespace() -> str | None:
    if sys.platform != "linux":
        return None
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    """The owner PID and its worker process group must both be absent to reap."""
    if sys.platform == "win32":
        # os.kill(pid, 0) calls TerminateProcess on Windows. Never signal a probe target.
        return _windows_pid_alive(pid)
    for probe in (os.kill, os.killpg):
        try:
            probe(pid, 0)
        except ProcessLookupError:
            continue
        except OSError:
            # EPERM and unknown errors cannot justify releasing a worker's claims.
            return True
        return True
    return False


def _windows_pid_alive(pid: int) -> bool:
    """Query a Windows process without acquiring any right to terminate it."""
    from ctypes import wintypes

    if not 0 < pid <= 0xFFFFFFFF:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows-only API.
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION only.
    if not handle:
        # ERROR_INVALID_PARAMETER means the PID is gone. Access denied (5) and
        # unknown errors cannot justify releasing a potentially live worker's claims.
        return ctypes.get_last_error() != 87  # type: ignore[attr-defined]  # Windows-only API.
    try:
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _sidecar_access_error(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    return code in (sqlite3.SQLITE_PERM, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN)


def _open_read_only(path: Path, options: str, deadline: float) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro" + options, uri=True,
        timeout=max(0, deadline - time.monotonic()), isolation_level=None,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        # Opening the connection and probing its first read share the caller's retry budget.
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        connection.execute(f"PRAGMA busy_timeout = {remaining_ms}")
        connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
    except BaseException:
        connection.close()
        raise
    return connection


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """Open the store without any possibility of writing it (no migration, no store creation).

    SQLite owns all locking. Prefer its native WAL handling, falling back when directory or sidecar
    permissions prevent that. Every source connection is mode=ro; snapshots are private to memory.
    """
    if not path.is_file():
        raise LedgerOpenError("unable to open database file", code="ledger.database_unavailable")
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    if os.access(path.parent, os.W_OK):
        try:
            return _open_read_only(path, "", deadline)
        except sqlite3.OperationalError as exc:
            if not _sidecar_access_error(exc):
                raise
    return _sidecar_read_only_connection(path, deadline)


def _sidecar_read_only_connection(path: Path, deadline: float, *, sidecar_free: bool = False) -> sqlite3.Connection:
    """Read without sidecar creation; strict views also forbid existing SHM writes.

    sidecar_free uses only an immutable source copied into memory, rejecting
    nonempty WALs and journals. It never opens a native SQLite WAL connection.
    """
    wal = Path(f"{path}-wal")
    journal = Path(f"{path}-journal")
    # A concurrent writer can change the file between checks; retry with a short backoff for up to the
    # same budget a writer gets from busy_timeout instead of surfacing a transient race to the caller.
    delay = 0.005
    message = "Task ledger changed while opening a read-only snapshot; retry the read"

    def check_budget(status: int, remaining: int, total: int) -> None:
        if time.monotonic() >= deadline:
            raise LedgerError(message, code="ledger.snapshot_changed")

    while time.monotonic() < deadline:
        if journal.exists():
            raise LedgerError("Task ledger has a rollback journal; retry after its writable owner completes recovery",
                code="ledger.rollback_journal")
        wal_before = wal.stat() if sidecar_free and wal.exists() else None
        if wal_before is not None and wal_before.st_size:
            raise LedgerError('Task ledger has a nonempty WAL; retry after its writable owner completes recovery '
                              'and checkpoints/closes the store (sidecar-free read refused)',
                code="ledger.wal_pending")
        if wal.exists() and not sidecar_free:
            try:
                return _open_read_only(path, "&readonly_shm=1", deadline)
            except sqlite3.OperationalError as exc:
                # Last-writer cleanup can remove sidecars between the existence check and open.
                # Retry as a snapshot only if the WAL disappeared; otherwise expose SQLite's error.
                if wal.exists() or not _sidecar_access_error(exc):
                    raise
        else:
            # A zero-byte WAL has no frames to recover. Keep its identity/stat
            # stable across the copy as well as the database's; never open it.
            before = path.stat()
            with closing(sqlite3.connect(
                path.as_uri() + "?mode=ro&immutable=1", uri=True,
                timeout=max(0, deadline - time.monotonic()),
            )) as source:
                snapshot = sqlite3.connect(":memory:", isolation_level=None)
                try:
                    source.backup(snapshot, pages=256, progress=check_budget, sleep=0.005)
                    after = path.stat()
                    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                    wal_after = wal.stat() if wal.exists() else None
                    stable_wal = (wal_before is None and wal_after is None) or (
                        wal_before is not None and wal_after is not None
                        and all(getattr(wal_before, key) == getattr(wal_after, key) for key in identity))
                    if (
                        all(getattr(before, key) == getattr(after, key) for key in identity)
                        and stable_wal and not journal.exists()
                    ):
                        snapshot.execute("PRAGMA query_only = ON")
                        return snapshot
                except BaseException:
                    snapshot.close()
                    raise
                snapshot.close()
        time.sleep(min(delay, max(0, deadline - time.monotonic())))
        delay = min(delay * 2, 0.1)
    raise LedgerError(message, code="ledger.snapshot_changed")


def _backup_before_migration(connection: sqlite3.Connection, path: Path) -> Path | None:
    """Copy an older ledger with SQLite's backup API before any writer migrates it, not only hipson init.

    A ledger is shared by every Hipson on the machine (and may be synced elsewhere); once migrated, older
    releases refuse it, so the pre-migration copy is the way back. Returns the backup path, or None.
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    row = connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone() if exists else None
    if row is None or row[0] >= SCHEMA_VERSION or row[0] not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    counter = 0
    while True:  # Never overwrite an earlier backup; a concurrent writer gets the next suffix.
        target = path.with_name(f"{path.name}.v{row[0]}-backup-{stamp}" + (f"-{counter}" if counter else ""))
        try:
            # The ledger holds briefs, journals and worker output: keep backups private, like init's.
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            counter += 1
            continue
        break
    try:
        with closing(sqlite3.connect(target)) as destination:
            connection.backup(destination)
            copied = destination.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
    except BaseException:
        target.unlink(missing_ok=True)  # A partial copy must not look like a good backup.
        raise
    if copied is None or copied[0] != row[0]:
        # Another writer migrated in between; its own backup holds the old version, this copy does not.
        target.unlink(missing_ok=True)
        return None
    return target


class Ledger:
    """One SQLite connection; callers must close it (or use contextlib.closing)."""

    def __init__(
        self,
        db: str | Path | None = None,
        *,
        create: bool = True,
        read_only: bool = False,
        sidecar_free: bool = False,
        hostname: Callable[[], str] = socket.gethostname,
        boot_id: Callable[[], str | None] = _boot_id,
        pid_namespace: Callable[[], str | None] = _pid_namespace,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self.path = Path(db).expanduser().resolve() if db is not None else default_ledger_db()
        self._hostname = hostname
        self._boot_id = boot_id
        self._pid_namespace = pid_namespace
        self._pid_alive = pid_alive
        if sidecar_free and not read_only:
            raise LedgerError('A sidecar-free connection requires read_only=True',
                code="ledger.sidecar_free_requires_read_only")
        if create and not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # mode=rw refuses to create a missing file, even if it disappears after a caller's existence check.
        target = self.path if create else self.path.as_uri() + "?mode=rw"
        if sidecar_free:
            self.connection = _sidecar_read_only_connection(
                self.path, time.monotonic() + BUSY_TIMEOUT_MS / 1000, sidecar_free=True)
        elif read_only:
            self.connection = _read_only_connection(self.path)
        else:
            self.connection = sqlite3.connect(
                target, uri=not create, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            self.connection.execute("PRAGMA foreign_keys = ON")
            if not read_only:
                self.connection.execute("PRAGMA journal_mode = WAL")
                _backup_before_migration(self.connection, self.path)
            with self._transaction(write=not read_only):
                if not read_only:
                    self.connection.execute(SCHEMA[0])
                version = self.connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
                if version is None and not read_only:
                    for statement in SCHEMA[1:]:
                        self.connection.execute(statement)
                    self.connection.execute("INSERT INTO schema_version VALUES (1, ?)", (SCHEMA_VERSION,))
                elif version is None or version[0] not in SUPPORTED_SCHEMA_VERSIONS:
                    found = version[0] if version is not None else None
                    raise LedgerError(f"Unsupported task ledger schema version {found}; expected {SCHEMA_VERSION}",
                        code="ledger.schema_unsupported")
                elif not read_only:
                    if version[0] == 1:
                        for statement in MIGRATION_V1_TO_V2:
                            self.connection.execute(statement)
                    if version[0] in (1, 2):
                        for statement in MIGRATION_V2_TO_V3:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3):
                        for statement in MIGRATION_V3_TO_V4:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4):
                        for statement in MIGRATION_V4_TO_V5:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5):
                        for statement in MIGRATION_V5_TO_V6:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5, 6):
                        for statement in MIGRATION_V6_TO_V7:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5, 6, 7):
                        for statement in MIGRATION_V7_TO_V8:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5, 6, 7, 8):
                        for statement in MIGRATION_V8_TO_V9:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5, 6, 7, 8, 9):
                        for statement in MIGRATION_V9_TO_V10:
                            self.connection.execute(statement)
                    if version[0] in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
                        for statement in MIGRATION_V10_TO_V11:
                            self.connection.execute(statement)
                    if version[0] < 12:
                        for statement in MIGRATION_V11_TO_V12:
                            self.connection.execute(statement)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def open_task(
        self,
        *,
        project: str | Path,
        title: str,
        objective: str,
        acceptance: Sequence[str] = (),
        lead: str = "claude",
        checks: Sequence[Sequence[str]] = (),
        needs: Sequence[str] = (),
        review_policy: str = "none",
    ) -> TaskView:
        if review_policy not in REVIEW_POLICIES:
            raise LedgerError(f"Review policy must be one of {REVIEW_POLICIES}", code="review.policy_invalid")
        project_path = str(Path(project).expanduser().resolve())
        if redact_text(project_path) != project_path:
            raise LedgerError("Project paths must not contain secret-shaped text", code="project.path_sensitive")
        title = redact_text(require_text(title, "title"))
        objective = redact_text(require_text(objective, "objective"))
        lead = redact_text(require_text(lead, "lead"))
        accepted = [redact_text(item) for item in acceptance]
        declared = validate_checks(checks)
        tags = validate_needs(needs)
        with self._transaction():
            number = self.connection.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 FROM tasks"
            ).fetchone()[0]
            task = f"T-{number:04d}"
            now = _timestamp()
            self.connection.execute(
                "INSERT INTO tasks (id, project, title, objective, acceptance, status, lead, created_at, updated_at,"
                " revision, checks, needs, review_policy) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 0, ?, ?, ?)",
                (task, project_path, title, objective, json.dumps(accepted), lead, now, now, json.dumps(declared),
                 json.dumps(tags), review_policy),
            )
            self._append_event(task, lead, "opened", title, [])
            if declared:
                self._append_event(task, lead, "checks_set", "acceptance checks: " + checks_text(declared), [])
            if review_policy == "required":
                # Fixed at open: no later write can drop it; only an audited done --force can bypass it.
                self._append_event(task, lead, "review_policy", "independent read-only review required before done", [])
            return self._view(task)

    def set_checks(self, task: str, *, checks: Sequence[Sequence[str]], actor: str, reason: str = "") -> TaskView:
        """Replace the declared acceptance checks; closure needs each one to pass fresh."""
        actor = redact_text(require_text(actor, "actor"))
        declared = validate_checks(checks)
        with self._transaction():
            current = self._task(task)
            if current["status"] in ("done", "cancelled"):
                raise LedgerError(f"Cannot change acceptance checks of {current['status']} task {task}",
                    code="checks.task_terminal")
            if current["checks"] and not reason.strip():
                raise LedgerError(f"Replacing declared acceptance checks of {task} requires --reason",
                    code="checks.replace_reason_required")
            self.connection.execute("UPDATE tasks SET checks = ? WHERE id = ?", (json.dumps(declared), task))
            summary = "acceptance checks: " + checks_text(declared)
            if current["checks"]:
                summary += f"; previously: {checks_text(current['checks'])}; reason: {redact_text(reason.strip())}"
            self._append_event(task, actor, "checks_set", summary, [])
            return self._view(task)

    def assign(
        self,
        task: str,
        *,
        actor: str,
        role: str,
        mode: str,
        scope: Sequence[str] = (),
        owner_pid: int | None = None,
        owner_boot_id: str | None = None,
        owner_host: str | None = None,
        owner_pid_namespace: str | None = None,
        lease_seconds: int | None = None,
        environment: dict[str, Any] | None = None,
        needs: Sequence[str] | None = None,
    ) -> Assignment:
        actor = redact_text(require_text(actor, "actor"))
        if role not in ROLES or mode not in MODES:
            raise LedgerError(f"Assignment requires role in {ROLES} and mode in {MODES}",
                code="assignment.role_or_mode_invalid")
        paths = validate_assignment_scope(scope, mode=mode)
        if owner_pid is not None and (type(owner_pid) is not int or not 1 <= owner_pid <= 2**31 - 1):
            raise LedgerError("owner_pid must be an integer between 1 and 2147483647",
                code="assignment.owner_pid_invalid")
        owner_boot_id = redact_text(require_text(owner_boot_id, "owner_boot_id")) if owner_boot_id is not None else None
        owner_host = redact_text(require_text(owner_host, "owner_host")) if owner_host is not None else None
        if owner_pid_namespace is not None and not isinstance(owner_pid_namespace, str):
            raise LedgerError("owner_pid_namespace must be a string", code="assignment.owner_namespace_invalid")
        owner_pid_namespace = redact_text(require_text(owner_pid_namespace, "owner_pid_namespace")) if owner_pid_namespace is not None else None
        if lease_seconds is not None and (type(lease_seconds) is not int or lease_seconds < 0):
            raise LedgerError("lease_seconds must be a nonnegative integer", code="assignment.lease_invalid")
        if environment is not None and (not isinstance(environment, dict)
                                        or environment.get("mode") not in ("inherit", "minimal")):
            raise LedgerError("environment must be an object with mode inherit or minimal",
                code="assignment.environment_invalid")
        tags = validate_needs(needs) if needs is not None else None
        result: Assignment | None = None
        conflict = ""
        with self._transaction():
            current = self._task(task)
            if current["status"] in ("done", "cancelled"):
                raise LedgerError(f"Cannot assign to terminal task {task} ({current['status']})",
                    code="assignment.task_terminal")
            now = _timestamp()
            try:
                lease_until = (
                    (datetime.fromisoformat(now) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
                    if lease_seconds is not None else None
                )
            except OverflowError as exc:
                raise LedgerError("lease_seconds is too large", code="assignment.lease_overflow") from exc
            if mode == "rw":
                holders = self.connection.execute(
                    """SELECT c.assignment, c.path, t.project AS project, a.* FROM claims c
                       JOIN assignments a ON a.id = c.assignment
                       JOIN tasks t ON t.id = a.task
                       WHERE a.status = 'active'
                       ORDER BY c.assignment, c.path""",
                ).fetchall()
                # The same project however its path is cased, where the filesystem ignores case.
                holders = [row for row in holders if _same_project(row["project"], current["project"])]
                reaped: set[str] = set()
                checked: set[str] = set()
                for holder in holders:
                    if holder["id"] in checked or not any(_claims_overlap(path, holder["path"]) for path in paths):
                        continue
                    checked.add(holder["id"])
                    item = self._assignment_dict(holder)
                    reason = self._orphan_reason(item)
                    if reason is not None:
                        self._reap_assignment(item, reason)
                        reaped.add(item["id"])
                holders = [holder for holder in holders if holder["id"] not in reaped]
                for path in paths:
                    holder = next((row for row in holders if _claims_overlap(path, row["path"])), None)
                    if holder is not None:
                        conflict = f"Claim {path!r} conflicts with {holder['path']!r} held by assignment {holder['assignment']}"
                        self._append_event(task, actor, "claim_conflict", conflict, [holder["assignment"], path])
                        break
            if not conflict:
                number = self.connection.execute(
                    "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 FROM assignments"
                ).fetchone()[0]
                assignment = f"A-{number:04d}"
                self.connection.execute(
                    """INSERT INTO assignments
                       (id, task, actor, role, mode, scope, status, started_at, ended_at,
                        owner_pid, owner_boot_id, owner_host, lease_until, owner_pid_namespace, environment, needs)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
                    (assignment, task, actor, role, mode, json.dumps(paths), now,
                     owner_pid, owner_boot_id, owner_host, lease_until, owner_pid_namespace,
                     # Names and counts only; redaction also covers a secret-shaped variable name.
                     json.dumps(redact_value(environment)) if environment is not None else None,
                     # The run's capability tags, or the task's when the run names none: routing history reads them.
                     json.dumps(tags if tags is not None else current["needs"])),
                )
                if mode == "rw":
                    self.connection.executemany(
                        "INSERT INTO claims VALUES (?, ?)", [(assignment, path) for path in paths]
                    )
                if current["status"] == "open":
                    self.connection.execute("UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task,))
                self._append_event(task, actor, "assigned", f"{assignment}: {role} ({mode})", [assignment, *paths])
                result = self._assignment(assignment)
        # Raise after committing the conflict event and any orphan recovery.
        # No new assignment or claims have been created in this branch.
        if result is None:
            raise ClaimConflictError(conflict + f"; inspect it with hipson task activity --assignment "
                                     f"{holder['assignment']} and wait, or run hipson task reap if its owner died",
                code="claim.conflict", fix=[
                    read_call("task.show", {"task": holder["task"], "section": "assignments"}, str(self.path)),
                    read_call("task.reap", {"task": holder["task"], "dry_run": True, "approved": True}, str(self.path)),
                ])
        return cast(Assignment, redact_value(result))

    def reap(self, *, task: str | None = None, dry_run: bool = False) -> list[ReapedAssignment]:
        """Recheck under the writer lock, committing each orphan independently."""
        with self._transaction(write=False):
            if task is not None:
                self._task(task)
            candidates = self.connection.execute(
                "SELECT id FROM assignments WHERE status = 'active' AND (? IS NULL OR task = ?) ORDER BY id",
                (task, task),
            ).fetchall()
        result: list[ReapedAssignment] = []
        for row in candidates:
            with self._transaction(write=not dry_run):
                current = self._assignment(row["id"])
                if current["status"] != "active":
                    continue
                reason = self._orphan_reason(current)
                if reason is not None:
                    item = current if dry_run else self._reap_assignment(current, reason)
                    result.append({"assignment": cast(Assignment, redact_value(item)), "reason": reason})
        return result

    def _orphan_reason(self, item: Assignment) -> str | None:
        expired = item["lease_until"] is not None and datetime.fromisoformat(item["lease_until"]) <= datetime.now(UTC)
        if item["owner_host"] is None or item["owner_host"] != self._hostname():
            return "lease_expired" if expired else None
        boot = self._boot_id() if item["owner_boot_id"] is not None else None
        if item["owner_boot_id"] is not None:
            if boot is not None and item["owner_boot_id"] != boot:
                return "boot_id_changed"
        # PIDs and process groups have meaning only in the owner's namespace.
        # Unknown local identity also cannot justify releasing namespaced claims.
        namespace = self._pid_namespace()
        if item["owner_pid_namespace"] is not None and item["owner_pid_namespace"] != namespace:
            return "lease_expired" if expired else None
        if item["owner_pid"] is not None:
            if not self._pid_alive(item["owner_pid"]):
                from hipson.task_posix import marked_descendant_alive
                if marked_descendant_alive(item["owner_pid"], str(self.path), item["id"]):
                    return None
                return "pid_gone"
            if boot is not None and item["owner_boot_id"] == boot and item["owner_pid_namespace"] == namespace:
                # A verified live PID or process group retains ownership beyond its lease.
                return None
        return "lease_expired" if expired else None

    def _reap_assignment(self, item: Assignment, reason: str) -> Assignment:
        from hipson.task_activity import bounded_journal
        now = _timestamp()
        activity = item['activity'] or {}
        last = activity.get('heartbeat_at') or 'unknown'
        owner = 'owner died' if reason in ('pid_gone', 'boot_id_changed') else 'owner liveness unknown'
        journal = bounded_journal({
            'result_state': 'none', 'exit_code': None, 'signal': None,
            'duration_seconds': max(0, (datetime.fromisoformat(now) - datetime.fromisoformat(item['started_at'])).total_seconds()),
            'changed_files': [], 'diff_stat': '', 'log_tail': activity.get('observations', []),
            'reason': f'{owner}; last activity at {last}; {reason}',
            'uncertainty': 'Supervisor unavailable; exit status and project changes were not observed.',
        })
        self.connection.execute(
            "UPDATE assignments SET status = 'cancelled', ended_at = ?, failure_journal = ?, usage = ? WHERE id = ?",
            (now, json.dumps(journal), json.dumps(_assignment_usage(
                {"source": None, "tokens": None, "reason": "runtime_error"}, item["started_at"], now)), item["id"]),
        )
        self.connection.execute("DELETE FROM claims WHERE assignment = ?", (item["id"],))
        self._append_event(item["task"], item["actor"], "reaped", f"{item['id']}: {journal['reason']}", [item["id"]])
        return self._assignment(item["id"])

    def record_changes(self, assignment: str, changes: dict[str, Any],
                       findings: Sequence[dict[str, str]] = ()) -> Assignment:
        """Record a post-run change observation once, with its findings and an 'observed' event, atomically."""
        if not isinstance(changes, dict) or changes.get("status") not in ("clean", "changes", "incomplete", "unobserved"):
            raise LedgerError("Change observation must be an object with a known status",
                code="observation.status_invalid")
        with self._transaction():
            current = self._assignment(assignment)
            if current["status"] == "active":
                raise LedgerError(f"Assignment {assignment} is still active", code="assignment.active")
            if current["changes"] is not None:
                raise LedgerError(f"Assignment {assignment} already has a change observation",
                    code="observation.already_recorded")
            self.connection.execute("UPDATE assignments SET changes = ? WHERE id = ?",
                                    (json.dumps(redact_value(changes)), assignment))
            ids = [self._insert_finding(current["task"], actor="hipson", severity="major", title=item["title"],
                                        detail=item["detail"], location=item["location"],
                                        source_assignment=assignment)["id"] for item in findings]
            counts = changes.get("counts", {})
            unexpected = sum(counts.get("out_of_scope", {}).values())
            listed = "; ".join(f"{item['attribution']} {item['change']} {item['path']}"
                               for item in changes.get("paths", [])[:20])
            omitted = max(0, len(changes.get("paths", [])) - 20) + int(changes.get("omitted", 0))
            self._append_event(
                current["task"], "hipson", "observed",
                f"{assignment}: changes {changes['status']}; {unexpected} outside scope, "
                f"{sum(counts.get('unattributed', {}).values())} unattributed"
                + ("; incomplete, not a clean result" if changes["status"] == "incomplete" else "")
                + (f"; paths: {listed}" if listed else "")
                + (f" (+{omitted} more in task show --json)" if omitted else "")
                + (f"; findings {', '.join(ids)}" if ids else ""),
                [assignment, *ids])
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def overlapping_writers(self, assignment: str, *, until: str | None = None) -> list[tuple[str, list[str]]]:
        """Other rw assignments in the same project alive during [start, until], with their scopes.

        until is when the final snapshot was taken (default: the end of this assignment, or now).
        """
        with self._transaction(write=False):
            current = self._assignment(assignment)
            project = self._task(current["task"])["project"]
            end = until or current["ended_at"] or _timestamp()
            rows = self.connection.execute(
                """SELECT a.id, a.scope FROM assignments a JOIN tasks t ON t.id = a.task
                   WHERE t.project = ? AND a.id <> ? AND a.mode = 'rw' AND a.started_at < ?
                   AND (a.ended_at IS NULL OR a.ended_at > ?) ORDER BY a.id""",
                (project, assignment, end, current["started_at"])).fetchall()
        return [(row["id"], json.loads(row["scope"])) for row in rows]

    def observe(self, assignment: str, activity: dict[str, Any]) -> None:
        """Runner observations never renew leases, reap owners, or release claims."""
        from hipson.task_activity import bounded_activity
        snapshot = bounded_activity(activity)
        with self._transaction():
            current = self._assignment(assignment)
            if current['status'] != 'active':
                raise LedgerError(f'Assignment {assignment} is already finished', code="assignment.already_finished")
            self.connection.execute('UPDATE assignments SET activity = ? WHERE id = ?',
                                    (json.dumps(snapshot), assignment))

    def activity(self, assignment: str) -> Assignment:
        with self._transaction(write=False):
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def ack(self, assignment: str, *, note: str = '', actor: str | None = None) -> Assignment:
        """Explicit, idempotent lead acknowledgement; never inferred from reads."""
        if actor is not None:
            actor = require_text(actor, "actor")
        with self._transaction():
            current = self._assignment(assignment, fix=[
                read_call("task.now", {"detail": "compact", "all": True}, str(self.path)),
            ])
            if current['status'] == 'active':
                raise LedgerError(f'Cannot acknowledge active assignment {assignment}', code="assignment.active")
            if current['acknowledged_at'] is None:
                from hipson.task_activity import safe_text
                self.connection.execute('UPDATE assignments SET acknowledged_at = ?, ack_note = ? WHERE id = ?',
                                        (_timestamp(), safe_text(note), assignment))
                self._append_event(current['task'], actor if actor is not None else self._task(current['task'])['lead'], 'acknowledged',
                                   f'{assignment}: {safe_text(note) or "result acknowledged"}', [assignment])
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def finish(
        self, assignment: str, *, result: str, summary: str, refs: Sequence[str] = (),
        worker_result: object = None,
        failure_journal: dict[str, Any] | None = None,
        usage: RuntimeUsage | None = None,
    ) -> Assignment:
        if result not in RESULTS:
            raise LedgerError(f"Assignment result must be one of {RESULTS}", code="assignment.result_invalid")
        summary = require_text(summary, "summary")
        report = validate_task_result(worker_result) if worker_result is not None else None
        from hipson.task_activity import bounded_journal
        journal = bounded_journal(failure_journal) if failure_journal is not None else None
        with self._transaction():
            current = self._assignment(assignment)
            if current["status"] != "active":
                raise LedgerError(f"Assignment {assignment} is already finished ({current['status']})",
                    code="assignment.already_finished")
            now = _timestamp()
            observed = _assignment_usage(usage if usage is not None else {
                "source": None, "tokens": None, "reason": "not_an_agent_runtime",
            }, current["started_at"], now)
            self.connection.execute(
                "UPDATE assignments SET status = ?, ended_at = ?, result = ?, failure_journal = ?, usage = ? WHERE id = ?",
                (result, now, json.dumps(report) if report is not None else None,
                 json.dumps(journal) if journal is not None else None, json.dumps(observed), assignment),
            )
            self.connection.execute("DELETE FROM claims WHERE assignment = ?", (assignment,))
            counts = ""
            if report is not None:
                if current["role"] == "reviewer":
                    for finding in report["findings"]:
                        self._insert_finding(
                            current["task"], actor=current["actor"], source_assignment=assignment,
                            severity=finding["severity"], title=finding["title"], detail=finding["detail"],
                            location=finding["location"] or "",
                        )
                counts = (
                    f" [worker_status={report['status']}; files_changed={len(report['files_changed'])}; "
                    f"commands={len(report['commands'])}; findings={len(report['findings'])}; risks={len(report['risks'])}]"
                )
            self._append_event(
                current["task"], current["actor"], "finished", f"{result}: {summary}{counts}", [assignment, *refs]
            )
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def add_finding(
        self, task: str, *, actor: str, severity: str, title: str, detail: str = "", location: str = "",
        source_assignment: str | None = None,
    ) -> Finding:
        actor = require_text(actor, "actor")
        title = require_text(title, "title")
        if severity not in FINDING_SEVERITIES:
            raise LedgerError(f"Finding severity must be one of {FINDING_SEVERITIES}", code="finding.severity_invalid")
        with self._transaction():
            self._task(task)
            self._check_finding_assignment(task, source_assignment)
            return self._insert_finding(
                task, actor=actor, severity=severity, title=title, detail=detail, location=location,
                source_assignment=source_assignment,
            )

    def _check_finding_assignment(self, task: str, assignment: str | None) -> None:
        if assignment is not None and self._assignment(assignment)["task"] != task:
            raise LedgerError(f"Assignment {assignment} does not belong to task {task}",
                code="assignment.task_mismatch")

    def _insert_finding(
        self, task: str, *, actor: str, severity: str, title: str, detail: str, location: str,
        source_assignment: str | None,
    ) -> Finding:
        """Caller owns the write transaction and validates the inputs."""
        number = self.connection.execute(
            "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 FROM findings"
        ).fetchone()[0]
        finding = f"F-{number:04d}"
        now = _timestamp()
        self.connection.execute(
            """INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, 'open', NULL, '', ?, ?, NULL)""",
            (finding, task, source_assignment, severity, redact_text(title), redact_text(detail),
             redact_text(location), now, now),
        )
        self._append_event(task, actor, "finding_opened", f"{finding} [{severity}]: {title}", [finding])
        self._reopen_for_finding(task, finding=finding, severity=severity, actor=actor)
        return self._finding(finding)

    def _reopen_for_finding(self, task: str, *, finding: str, severity: str, actor: str) -> None:
        """Keep closure and finding writes atomic under the caller's writer lock."""
        if severity not in ("blocking", "major"):
            return
        changed = self.connection.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ? AND status = 'done'", (task,)
        ).rowcount
        if changed:
            self._append_event(task, actor, "reopened", f"done -> in_progress: open {severity} finding {finding}", [finding])

    def update_finding(
        self, finding: str, *, status: str, actor: str, resolution_note: str = "",
        resolved_by_assignment: str | None = None,
    ) -> Finding:
        actor = require_text(actor, "actor")
        if status not in FINDING_STATUSES:
            raise LedgerError(f"Finding status must be one of {FINDING_STATUSES}", code="finding.status_invalid")
        with self._transaction():
            current = self._finding(finding)
            previous = current["status"]
            if previous == status or (previous != "open" and status != "open"):
                raise LedgerError(f"Invalid finding transition for {finding}: {previous} -> {status}",
                    code="finding.transition_not_allowed")
            if status == "open":
                resolution_note = require_text(resolution_note, "Reopening note")
                if resolved_by_assignment is not None:
                    raise LedgerError("A reopened finding cannot have a resolved-by assignment",
                        code="finding.reopen_resolution")
            # A review task may delegate its fix to a separate task. Keep the FK
            # validated, and record that task in the immutable resolution event.
            fix_task = self._assignment(resolved_by_assignment)["task"] if resolved_by_assignment is not None else None
            now = _timestamp()
            self.connection.execute(
                """UPDATE findings SET status = ?, resolved_by_assignment = ?, resolution_note = ?,
                   updated_at = ?, resolved_at = ? WHERE id = ?""",
                (status, resolved_by_assignment, redact_text(resolution_note), now,
                 None if status == "open" else now, finding),
            )
            self._append_event(
                current["task"], actor, "finding_reopened" if status == "open" else "finding_resolved",
                f"{finding}: {previous} -> {status}" + (f": {resolution_note}" if resolution_note else "")
                + (f" [fix task={fix_task}]" if fix_task and fix_task != current["task"] else ""),
                [finding, *([resolved_by_assignment] if resolved_by_assignment else []),
                 *([fix_task] if fix_task and fix_task != current["task"] else [])],
            )
            if status == "open":
                self._reopen_for_finding(current["task"], finding=finding, severity=current["severity"], actor=actor)
            return cast(Finding, redact_value(self._finding(finding)))

    def findings(
        self, *, task: str | None = None, status: str | None = None, severity: str | None = None,
    ) -> list[Finding]:
        if status is not None and status not in FINDING_STATUSES:
            raise LedgerError(f"Finding status must be one of {FINDING_STATUSES}", code="finding.status_invalid")
        if severity is not None and severity not in FINDING_SEVERITIES:
            raise LedgerError(f"Finding severity must be one of {FINDING_SEVERITIES}", code="finding.severity_invalid")
        with self._transaction(write=False):
            if task is not None:
                self._task(task)
            return cast(list[Finding], redact_value(self._findings(task=task, status=status, severity=severity)))

    def _findings(
        self, *, task: str | None = None, status: str | None = None, severity: str | None = None,
    ) -> list[Finding]:
        # A long-lived legacy reader can observe a later writer's migration.
        if self.connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()[0] < 3:
            return []
        return [cast(Finding, dict(row)) for row in self.connection.execute(
            """SELECT * FROM findings WHERE (? IS NULL OR task = ?) AND (? IS NULL OR status = ?)
               AND (? IS NULL OR severity = ?) ORDER BY created_at, id""",
            (task, task, status, status, severity, severity),
        )]

    def _finding(self, finding: str) -> Finding:
        row = self.connection.execute("SELECT * FROM findings WHERE id = ?", (finding,)).fetchone()
        if row is None:
            raise LedgerError(f"Finding not found: {finding}", code="finding.not_found")
        return cast(Finding, dict(row))

    def add_event(self, task: str, *, actor: str, type: str, summary: str, refs: Sequence[str] = ()) -> TaskEvent:
        actor = require_text(actor, "actor")
        type = require_text(type, "type")
        summary = require_text(summary, "summary")
        if reserved_event_type(type):
            raise LedgerError(f"Event type {type!r} is reserved for ledger-recorded transitions; "
                              "use a descriptive type such as progress or note", code="event.type_reserved")
        with self._transaction():
            self._task(task)
            return self._append_event(task, actor, type, summary, refs)

    def record_route(self, task: str, *, actor: str, summary: str, refs: Sequence[str] = ()) -> TaskEvent:
        """Record an actor recommendation so later outcomes can be traced back to the decision."""
        actor = redact_text(require_text(actor, "actor"))
        with self._transaction():
            self._task(task)
            return self._append_event(task, actor, "routed", redact_text(require_text(summary, "summary")),
                                      [redact_text(ref) for ref in refs])

    def record_review_tree(self, assignment: str, *, phase: str, revision: str, fingerprint: str | None,
                           source: str = "live") -> TaskEvent:
        """The runner's record of the tree a reviewer saw when it started and when it ended (reserved type).

        source is "snapshot" when the fingerprint was re-hashed from the reviewer's immutable snapshot, "live" when
        it is the project's working tree (which a required review never accepts)."""
        if phase not in ("start", "end"):
            raise LedgerError("Review tree phase is start or end", code="review.tree_phase_invalid")
        if source not in ("live", "snapshot"):
            raise LedgerError("Review tree source is live or snapshot", code="review.tree_source_invalid")
        with self._transaction():
            current = self._assignment(assignment)
            if current["role"] != "reviewer" or current["status"] != "active":
                raise LedgerError(f"Assignment {assignment} is not an active reviewer",
                    code="review.assignment_not_active")
            return self._append_event(current["task"], "hipson", "reviewed",
                                      f"{assignment}: {phase} revision={revision or 'none'}; "
                                      f"fingerprint={fingerprint or 'none'}; source={source}", [assignment])

    def record_flow(self, task: str, *, actor: str, summary: str, refs: Sequence[str] = ()) -> TaskEvent:
        """Record a hipson task flow stage or stop; a reserved type, so free-form events cannot forge one."""
        actor = redact_text(require_text(actor, "actor"))
        with self._transaction():
            self._task(task)
            return self._append_event(task, actor, "flow", redact_text(require_text(summary, "summary")),
                                      [redact_text(ref) for ref in refs])

    def record_checkpoint(self, assignment: str, *, type: str, summary: str) -> TaskEvent:
        """Record a host-owned checkpoint or restore of an assignment's scope (counts, never contents)."""
        if type not in ("checkpointed", "restored"):
            raise LedgerError("Checkpoint events are checkpointed or restored", code="checkpoint.event_invalid")
        with self._transaction():
            current = self._assignment(assignment)
            return self._append_event(current["task"], "hipson", type,
                                      redact_text(require_text(summary, "summary")), [assignment])

    def set_status(
        self, task: str, *, to: str, actor: str, summary: str = "", force: bool = False, reason: str = "",
    ) -> TaskView:
        actor = require_text(actor, "actor")
        validate_status_override(to=to, force=force, reason=reason)
        with self._transaction():
            return self._set_status_locked(task, to=to, actor=actor, summary=summary, force=force, reason=reason)

    def close_task(self, task: str, *, to: str = "done", actor: str, summary: str = "",
                   expected_checks: Sequence[Sequence[str]] | None = None) -> TaskView:
        """Move through review (and done) in one transaction; any refused gate rolls back every step."""
        actor = require_text(actor, "actor")
        if to not in ("review", "done"):
            raise LedgerError("Close target must be review or done", code="close.target_invalid")
        with self._transaction():
            view = self._view(task)
            status = view["status"]
            if status not in ("open", "in_progress", "review") or (status == "review" and to == "review"):
                raise CloseRefusedError(
                    task, [f"{task} is {status}; close works from open, in_progress or review"
                           + ("" if status != "review" else " (use --to done)")], code="close.status_not_allowed")
            reasons = [] if view["verification"]["passing"] else list(view["verification"]["reasons"])
            if expected_checks is not None and view["checks"] != [list(check) for check in expected_checks]:
                reasons.append("declared checks differ from the ones this close expects: " + checks_text(view["checks"]))
            blockers = done_blockers(view)
            # Review must not race a writer assigned after the caller's own checks either.
            reasons += blockers if to == "done" else [item for item in blockers if item.startswith("active assignment")]
            if reasons:
                raise close_refusal(view, reasons, self.path)
            if status == "open":
                self._set_status_locked(task, to="in_progress", actor=actor, summary="started by close",
                                        force=False, reason="")
            if status != "review":
                view = self._set_status_locked(task, to="review", actor=actor, summary=summary, force=False, reason="")
            if to == "done":
                view = self._set_status_locked(task, to="done", actor=actor, summary=summary, force=False, reason="")
            return view

    def _set_status_locked(
        self, task: str, *, to: str, actor: str, summary: str, force: bool, reason: str,
    ) -> TaskView:
        current = self._task(task)
        previous = current["status"]
        if to not in TRANSITIONS[previous]:
            allowed = ', '.join(sorted(TRANSITIONS[previous])) or 'none (terminal)'
            hint = f"; hipson task close --task {task} verifies and closes in one step" if to == 'done' else ''
            raise LedgerError(f"Invalid task transition for {task}: {previous} -> {to} (allowed: {allowed}){hint}",
                code="status.transition_not_allowed", fix=[
                    read_call("task.show", {"task": task, "detail": "compact"}, str(self.path)),
                    # Terminal targets are lead decisions (cancelled cannot be undone; done runs the gate): the
                    # fix offers only the non-terminal moves and leaves the rest to the shown task.
                    *(read_call("task.status", {"task": task, "to": target, "actor": current["lead"], "approved": True},
                                str(self.path)) for target in sorted(TRANSITIONS[previous] - TERMINAL_STATUSES)),
                ])
        refs: list[str] = []
        view = self._view(task)
        verification = view['verification']
        blockers = []
        if to in ('review', 'done') and not verification['passing']:
            blockers.extend(verification['reasons'])
        if to == "done":
            active = [item for item in view["assignments"] if item["status"] == "active"]
            severe = [item for item in view["findings"] if item["severity"] in ("blocking", "major")]
            unread = [item for item in view['assignments'] if item['ack_state'] == 'unacknowledged']
            blockers += [f"active assignment {item['id']} ({item['mode']})" for item in active]
            blockers += [f"claim {item['assignment']}:{item['path']}" for item in view["claims"]]
            blockers += [f"open {item['severity']} finding {item['id']}: {item['title']}" for item in severe]
            blockers += [f"unacknowledged result {item['id']}" for item in unread]
            blockers += [] if view["review"]["passing"] else view["review"]["reasons"]
            if force:
                blocker_refs = list(dict.fromkeys([
                    *[item["id"] for item in active], *[item["assignment"] for item in view["claims"]],
                    *[item["id"] for item in severe],
                    *[item['id'] for item in unread],
                ]))
                self._append_event(task, actor, "done_override",
                                   f"Reason: {reason.strip()}; blockers: " + ("; ".join(blockers) or "none"),
                                   blocker_refs)
            remaining = [item for item in view["findings"] if item["severity"] in ("minor", "info")]
            refs += [item["id"] for item in remaining]
            summary = f"{previous} -> done" + (f": {summary}" if summary else "")
            summary += "; remaining minor/info findings: " + (
                "; ".join(f"{item['id']} [{item['severity']}]: {item['title']}" for item in remaining) or "none"
            )
        if to in ('review', 'done'):
            if blockers and not force:
                hint = (f"; next: hipson task close --task {task} (runs declared checks), or "
                        f"hipson task verify --task {task} -- COMMAND (MCP: task.close / task.verify)"
                        if not verification['passing'] else '')
                refusal = close_refusal(view, blockers, self.path)
                raise GateRefusedError(f"Cannot mark {task} {to}: " + "; ".join(blockers) + hint,
                                       reason_codes=refusal.reason_codes, fix=refusal.fix)
            if force and to == 'review':
                self._append_event(task, actor, 'review_override',
                                   f"Reason: {reason.strip()}; blockers: " + ('; '.join(blockers) or 'none'), [])
            accepted_ids = verification['accepted_ids']
            refs.extend(accepted_ids)
            summary = summary or f'{previous} -> {to}'
            summary += '; lead verification: ' + (', '.join(accepted_ids)
                                                  or 'OVERRIDDEN: ' + '; '.join(verification['reasons']))
            summary += '; acceptance checks: ' + checks_text(view['checks'])
        self.connection.execute("UPDATE tasks SET status = ? WHERE id = ?", (to, task))
        if to == 'cancelled':
            summary = f'{previous} -> cancelled' + (f': {summary}' if summary else '')
        final = self._view(task)
        if to in ('review', 'done') and not final['verification']['passing']:
            if not force:
                reasons = final['verification']['reasons']
                refusal = close_refusal(final, reasons, self.path)
                raise GateRefusedError(f"Cannot mark {task} {to}: " + '; '.join(reasons),
                                       reason_codes=refusal.reason_codes, fix=refusal.fix)
            if final['verification']['reasons'] != verification['reasons']:
                self._append_event(task, actor, to + '_override',
                                   f"Reason: {reason.strip()}; final verification blockers: "
                                   + '; '.join(final['verification']['reasons']), [])
                refs = [ref for ref in refs if ref not in verification['accepted_ids']]
                summary += '; final lead verification OVERRIDDEN: ' + '; '.join(final['verification']['reasons'])
        self._append_event(task, actor, "status", summary or f"{previous} -> {to}", refs)
        stored = self._task(task)
        final['revision'], final['updated_at'] = stored['revision'], stored['updated_at']
        return final

    def show(self, task: str) -> TaskView:
        with self._transaction(write=False):
            return self._view(task)

    def _builder_finished_seq(self, task: str) -> int:
        builders = {row['id'] for row in self.connection.execute(
            "SELECT id FROM assignments WHERE task = ? AND role = 'builder' AND status <> 'active'", (task,))}
        return max((row['seq'] for row in self.connection.execute(
            "SELECT seq, refs FROM events WHERE task = ? AND type IN ('finished', 'reaped')", (task,))
                    if builders.intersection(json.loads(row['refs']))), default=0)

    def verifications(self, task: str) -> list[dict[str, Any]]:
        # Read old stores without migrating them or inventing evidence from legacy events.
        exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'verifications'").fetchone()
        return [json.loads(row['evidence']) for row in self.connection.execute(
            'SELECT evidence FROM verifications WHERE task = ? ORDER BY seq', (task,))] if exists else []

    def _record_verification(self, evidence: dict[str, Any]) -> None:
        """Called only by the lead-run command supervisor, never by finish/event ingestion."""
        with self._transaction():
            self._task(evidence['task'])
            self.connection.execute('INSERT INTO verifications (id, task, evidence) VALUES (?, ?, ?)',
                                    (evidence['id'], evidence['task'], json.dumps(evidence)))
            self._append_event(evidence['task'], evidence.get('actor', 'unknown'), 'lead_verified',
                               f"{evidence['id']}: exit={evidence['exit_code']}; revision={evidence['revision']}; "
                               f"fingerprint={evidence['fingerprint']}; changed={evidence['state_changed']}",
                               [evidence['id']])

    def events(self, task: str, *, after: int = 0) -> list[TaskEvent]:
        if after < 0 or after > SQLITE_MAX_INTEGER:
            raise LedgerError(f"Event cursor --after must be between 0 and {SQLITE_MAX_INTEGER}",
                code="event.cursor_invalid")
        with self._transaction(write=False):
            self._task(task)
            return [
                self._event_dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM events WHERE task = ? AND seq > ? ORDER BY seq", (task, after)
                )
            ]

    def list_tasks(self, *, project: str | Path | None = None, status: str | None = None) -> list[Task]:
        if status is not None and status not in STATUSES:
            raise LedgerError(f"Task status must be one of {STATUSES}", code="status.value_invalid")
        project_path = str(Path(project).expanduser().resolve()) if project is not None else None
        return cast(list[Task], redact_value([
            self._task_dict(row)
            for row in self.connection.execute(
                """SELECT * FROM tasks WHERE (? IS NULL OR project = ?) AND (? IS NULL OR status = ?)
                   ORDER BY created_at, id""",
                (project_path, project_path, status, status),
            )
        ]))

    def projection_rows(self, *, task: str | None = None, project: str | Path | None = None,
                        include_terminal: bool) -> list[dict[str, Any]]:
        """Load compact read inputs in batches from one SQLite snapshot.

        include_terminal has no default: every caller states it, and a task lookup always includes terminal tasks.
        """
        project_path = str(Path(project).expanduser().resolve()) if project is not None else None
        with self._transaction(write=False):
            version = self.connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()[0]
            checks = "checks" if version >= 7 else "'[]' AS checks"
            policy = "review_policy" if version >= 11 else "'none' AS review_policy"
            unread = "OR (COALESCE(acknowledged_at, '') = '' AND ack_note <> ?)" if version >= 5 else ""
            tasks = {row["id"]: dict(row) for row in self.connection.execute(
                f"""SELECT id, project, title, objective, status, lead, created_at, updated_at, {checks}, {policy}
                    FROM tasks WHERE (? IS NULL OR id = ?) AND (? IS NULL OR project = ?)
                      AND (? OR status NOT IN ('done', 'cancelled') OR EXISTS (
                          SELECT 1 FROM assignments WHERE task = tasks.id AND (status = 'active' {unread})))
                    ORDER BY created_at, id""",  # nosec B608
                (task, task, project_path, project_path, task is not None or include_terminal,
                 *((LEGACY_ACK_NOTE,) if version >= 5 else ())),
            )}
            if task is not None and not tasks:
                raise LedgerError(
                    f"Task not found: {task} (list tasks with hipson task list or hipson task now --all)",
                    code="task.not_found", fix=task_lookup_calls(self.path))

            def select(sql: str, *args: Any) -> Iterator[sqlite3.Row]:
                # The only interpolation here is a bounded list of parameter placeholders.
                ids = list(tasks)
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    yield from self.connection.execute(sql.format(ids=", ".join("?" for _ in batch)), (*batch, *args))

            for row in tasks.values():
                row.update(checks=json.loads(row["checks"]), assignments=[], verifications=[], findings=[],
                           last_event_at=None, assigned={}, delivery={}, closure=None, review_trees={},
                           latest_verification_seq=0, verification_events=[])
            for event in select(
                """SELECT * FROM (
                       SELECT task, seq, type, summary, refs,
                              MAX(timestamp) OVER (PARTITION BY task) AS last_event_at,
                              MAX(seq) OVER (PARTITION BY task) AS last_seq
                       FROM events WHERE task IN ({ids})
                   ) WHERE seq = last_seq OR type IN ('assigned', 'lead_verified', 'reviewed', 'finished', 'reaped', 'status')
                   ORDER BY seq"""
            ):
                row = tasks[event["task"]]
                row["last_event_at"] = event["last_event_at"]
                refs = json.loads(event["refs"])
                if event["type"] == "assigned" and refs:
                    row["assigned"].setdefault(refs[0], event["seq"])
                elif event["type"] in ("finished", "reaped"):
                    row["delivery"].update(dict.fromkeys(refs, event["seq"]))
                elif event["type"] == "status" and any(
                    event["summary"].startswith(f"{previous} -> {row['status']}") for previous in STATUSES
                ):
                    row["closure"] = event["seq"]
                elif event["type"] == "lead_verified":
                    row["latest_verification_seq"] = event["seq"]
                    match = VERIFIED_PATTERN.search(event["summary"])
                    tree = match.groups() if match else ("", "")
                    row["verification_events"].append((refs, tree))
                elif event["type"] == "reviewed" and refs and (match := REVIEWED_PATTERN.search(event["summary"])):
                    phase, revision, fingerprint, source = match.groups()
                    row["review_trees"].setdefault(refs[0], {}).setdefault(phase, (revision, fingerprint, source or "live"))

            result = ("json_extract(result, '$.status') AS result_status, "
                      "json_extract(result, '$.summary') AS result_summary" if version >= 3 else
                      "NULL AS result_status, NULL AS result_summary")
            activity = ("acknowledged_at, ack_note, json_extract(activity, '$.heartbeat_at') AS heartbeat_at"
                        if version >= 5 else "NULL AS acknowledged_at, NULL AS ack_note, NULL AS heartbeat_at")
            usage = "usage" if version >= 12 else "NULL AS usage"
            for assignment in select(
                f"""SELECT task, id, actor, role, mode, status, started_at, ended_at, {result}, {activity}, {usage}
                    FROM assignments WHERE task IN ({{ids}}) ORDER BY started_at, id"""  # nosec B608
            ):
                row = tasks[assignment["task"]]
                delivery = row["delivery"].get(assignment["id"])
                historical = (row["status"] in ("done", "cancelled") and row["closure"] is not None
                              and delivery is not None and delivery < row["closure"])
                # Match the board's acknowledgement semantics, including outcomes delivered before closure.
                ack_state = ("not_required" if assignment["status"] == "active" else
                             "acknowledged" if assignment["acknowledged_at"] else "not_required"
                             if version < 5 or assignment["ack_note"] == LEGACY_ACK_NOTE or historical else
                             "unacknowledged")
                row["assignments"].append({**dict(assignment), "ack_state": ack_state})

            for task_id, row in list(tasks.items()):
                row["has_active"] = any(item["status"] == "active" for item in row["assignments"])
                row["has_unacknowledged"] = any(item["ack_state"] == "unacknowledged" for item in row["assignments"])
                if (task is None and not include_terminal and row["status"] in ("done", "cancelled")
                        and not (row["has_active"] or row["has_unacknowledged"])):
                    del tasks[task_id]
                    continue
                row["builder_finished_seq"] = max(
                    (row["delivery"].get(item["id"], 0) for item in row["assignments"]
                     if item["role"] == "builder" and item["status"] != "active"), default=0)

            if version >= 3:
                for finding in select(
                    """SELECT task, id, severity, total FROM (
                           SELECT task, id, severity, COUNT(*) OVER by_severity AS total,
                                  ROW_NUMBER() OVER (by_severity ORDER BY created_at, id) AS position
                           FROM findings WHERE task IN ({ids}) AND status = 'open'
                           WINDOW by_severity AS (PARTITION BY task, severity)
                       ) WHERE position <= CASE WHEN severity IN ('blocking', 'major') THEN 10 ELSE 1 END
                       ORDER BY position"""
                ):
                    tasks[finding["task"]]["findings"].append(dict(finding))

            if version >= 6:
                # Rank just the identities, then extract display fields and inspect metadata on the selected rows.
                # An unidentifiable record has its own group so it can conservatively shadow older evidence.
                declared = "json_array_length(tasks.checks) > 0" if version >= 7 else "0"
                for verification in select(
                    f"""WITH identities AS (
                            SELECT seq, v.task, CASE WHEN {declared} THEN (
                                SELECT CASE WHEN type = 'text' AND length(CAST(value AS BLOB)) = 64
                                                  AND value NOT GLOB '*[^0-9a-f]*' THEN value END
                                FROM json_each(CASE WHEN json_valid(evidence) THEN evidence END)
                                WHERE key = 'argv_sha256' ORDER BY id DESC LIMIT 1
                            ) END AS identity
                            FROM verifications v JOIN tasks ON tasks.id = v.task WHERE v.task IN ({{ids}})
                        ), latest AS (
                            SELECT MAX(seq) AS seq, identity FROM identities GROUP BY task, identity
                        ), selected AS (
                            SELECT v.task, v.id, v.seq, latest.identity, CASE WHEN json_valid(evidence) THEN
                                CASE WHEN json_type(evidence) = 'object' THEN evidence END END AS body
                            FROM latest JOIN verifications v USING (seq)
                        )
                        SELECT task, id, seq, identity,
                               CAST(json_extract(body, '$.id', '$.command', '$.finished_at', '$.revision',
                                   '$.exit_code', '$.builder_finished_seq', '$.state_changed', '$.actor', '$.context',
                                   '$.cleanup_confirmed', '$.fingerprint', '$.coverage', '$.error') AS BLOB) AS fields,
                               json_type(body, '$.files') AS files_type,
                               json_type(body, '$.metadata_fallback_paths') AS fallback_type,
                               EXISTS (SELECT 1 FROM json_each(body, '$.metadata_fallback_paths')
                                       WHERE type <> 'text') AS malformed_fallback,
                               EXISTS (SELECT 1 FROM json_each(body, '$.files') AS file WHERE
                                   CASE WHEN file.type <> 'object' THEN 1 ELSE
                                       json_type(file.value, '$.path') IS NOT 'text'
                                       OR json_type(file.value, '$.state') IS NOT 'text'
                                       OR json_type(file.value, '$.key') NOT IN ('null', 'text') END
                               ) AS malformed_files,
                               EXISTS (SELECT 1 FROM json_each(body)
                                       GROUP BY key HAVING COUNT(*) > 1) AS duplicate_fields,
                               EXISTS (SELECT 1 FROM json_each(body, '$.metadata_fallback_paths')) OR EXISTS (
                                   SELECT 1 FROM json_each(body, '$.files') AS file WHERE
                                       CASE WHEN file.type = 'object' THEN
                                           instr(json_extract(file.value, '$.state'), ':metadata:') > 0 END
                               ) AS metadata_fallback
                        FROM selected ORDER BY seq""",  # nosec B608
                ):
                    tasks[verification["task"]]["verifications"].append(dict(verification))
        return list(tasks.values())

    def board(self) -> list[TaskView]:
        with self._transaction(write=False):
            # A finished task stays on the board only while one of its assignments is live or unacknowledged.
            # Decide that before building the view: a view fingerprints the task's project, and the whole task
            # history must not pay for it (one large or slow project folder of an old task hung the board).
            states: dict[str, dict[str, Any]] = {}
            views = [self._view(row['id'], active_only=True, states=states) for row in self.connection.execute(
                'SELECT id, status FROM tasks ORDER BY created_at, id').fetchall()
                if row['status'] not in ('done', 'cancelled') or self._has_live_assignment(row['id'])]
            return [view for view in views if view['status'] not in ('done', 'cancelled') or view['assignments']]

    def _has_live_assignment(self, task: str) -> bool:
        """The board's own assignment filter (see _view active_only), without fingerprinting the project."""
        return any(item['status'] == 'active' or item['ack_state'] == 'unacknowledged'
                   for item in (self._assignment_dict(row) for row in self.connection.execute(
                       'SELECT * FROM assignments WHERE task = ?', (task,)).fetchall()))

    def lead_snapshot(self, *, project: str | Path | None = None) -> list[tuple[TaskView, list[TaskEvent]]]:
        """Read views and events at one ledger revision, without migration or recovery."""
        project_path = str(Path(project).expanduser().resolve()) if project is not None else None
        with self._transaction(write=False):
            states: dict[str, dict[str, Any]] = {}
            return [
                (self._view(row['id'], states=states), [self._event_dict(event) for event in self.connection.execute(
                    'SELECT * FROM events WHERE task = ? ORDER BY seq', (row['id'],))])
                for row in self.connection.execute(
                    'SELECT id FROM tasks WHERE (? IS NULL OR project = ?) ORDER BY id',
                    (project_path, project_path),
                )
            ]

    def _task(self, task: str) -> Task:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task,)).fetchone()
        if row is None:
            raise LedgerError(
                f"Task not found: {task} (list tasks with hipson task list or hipson task now --all)",
                code="task.not_found", fix=task_lookup_calls(self.path))
        return self._task_dict(row)

    def _assignment(self, assignment: str, *, fix: list[ReadCall] | None = None) -> Assignment:
        row = self.connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment,)).fetchone()
        if row is None:
            raise LedgerError(f"Assignment not found: {assignment}", code="assignment.not_found", fix=fix)
        return self._assignment_dict(row)

    def _view(self, task: str, *, active_only: bool = False, states: dict[str, dict[str, Any]] | None = None) -> TaskView:
        current = self._task(task)
        from hipson.task_verification import project_state, verification_state
        records = self.verifications(task)
        # One read (board, lead snapshot) fingerprints each project folder once, however many tasks share it.
        # verification_state may annotate the state it gets, so every view receives its own copy.
        if states is None:
            state = project_state(current['project'], self.path)
        else:
            if current['project'] not in states:
                states[current['project']] = project_state(current['project'], self.path)
            state = copy.deepcopy(states[current['project']])
        builder_seq = self._builder_finished_seq(task)
        verification = verification_state(records, state, builder_seq, current['lead'], current['checks'])
        assignments = [
            self._assignment_dict(row)
            for row in self.connection.execute(
                "SELECT * FROM assignments WHERE task = ? ORDER BY started_at, id",
                (task,),
            )
        ]
        review = review_state(current['review_policy'], current['lead'], assignments,
                              *self._review_seqs(task, verification['accepted_ids'], builder_seq))
        if active_only:
            assignments = [item for item in assignments if item['status'] == 'active' or item['ack_state'] == 'unacknowledged']
        claims: list[Claim] = [
            {"assignment": row["assignment"], "path": row["path"]}
            for row in self.connection.execute(
                """SELECT c.* FROM claims c JOIN assignments a ON a.id = c.assignment
                   WHERE a.task = ? ORDER BY c.assignment, c.path""",
                (task,),
            )
        ]
        return cast(TaskView, redact_value({**current, "assignments": assignments, "claims": claims,
                                          "findings": self._findings(task=task, status="open"),
                                          "verification": verification, "review": review}))

    def _review_seqs(self, task: str, accepted: Sequence[str], builder_seq: int) -> tuple[
            dict[str, int], int, int, dict[str, dict[str, tuple[str, ...]]], tuple[str, str] | None]:
        """What review_state compares: assignment starts, the accepted verification and the reviewed trees."""
        assigned: dict[str, int] = {}
        verified = 0
        verified_tree: tuple[str, str] | None = None
        trees: dict[str, dict[str, tuple[str, ...]]] = {}
        for row in self.connection.execute(
                "SELECT seq, type, summary, refs FROM events WHERE task = ? AND type IN "
                "('assigned', 'lead_verified', 'reviewed') ORDER BY seq", (task,)):
            refs = json.loads(row['refs'])
            if row['type'] == 'assigned' and refs:
                assigned.setdefault(refs[0], row['seq'])
            elif row['type'] == 'lead_verified' and set(refs) & set(accepted):
                verified = max(verified, row['seq'])
                found = VERIFIED_PATTERN.search(row['summary'])
                verified_tree = (found.group(1), found.group(2)) if found else ('', '')
            elif row['type'] == 'reviewed' and refs and (match := REVIEWED_PATTERN.search(row['summary'])):
                trees.setdefault(refs[0], {}).setdefault(match.group(1), (match.group(2), match.group(3),
                                                                          match.group(4) or 'live'))
        return assigned, builder_seq, verified, trees, verified_tree

    def _append_event(self, task: str, actor: str, type: str, summary: str, refs: Sequence[str]) -> TaskEvent:
        now = _timestamp()
        cursor = self.connection.execute(
            "INSERT INTO events (task, actor, type, summary, refs, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (task, redact_text(actor), redact_text(type), redact_text(summary), json.dumps(redact_value(list(refs))), now),
        )
        self.connection.execute("UPDATE tasks SET revision = revision + 1, updated_at = ? WHERE id = ?", (now, task))
        row = self.connection.execute("SELECT * FROM events WHERE seq = ?", (cursor.lastrowid,)).fetchone()
        return self._event_dict(row)

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> Task:
        return {
            "artifact_kind": "hipson.task",
            "schema_version": "1.0",
            "id": row["id"],
            "project": row["project"],
            "title": row["title"],
            "objective": row["objective"],
            "acceptance": json.loads(row["acceptance"]),
            "checks": json.loads(row["checks"]) if "checks" in row.keys() else [],
            "needs": json.loads(row["needs"]) if "needs" in row.keys() else [],
            "review_policy": row["review_policy"] if "review_policy" in row.keys() else "none",
            "status": row["status"],
            "lead": row["lead"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
        }

    def _assignment_dict(self, row: sqlite3.Row) -> Assignment:
        acknowledged = row['acknowledged_at'] if 'acknowledged_at' in row.keys() else None
        note = row['ack_note'] if 'ack_note' in row.keys() else ''
        legacy = 'acknowledged_at' not in row.keys() or note == LEGACY_ACK_NOTE
        task_status = self._task(row['task'])['status']
        terminal = task_status in ('done', 'cancelled')
        historical = False
        if terminal and not legacy and not acknowledged and row['status'] != 'active':
            # Task updated_at changes on delivery, and timestamps may tie. Use event
            # sequence order to exempt only outcomes already present at closure.
            # Generic status observations can follow closure; only a recorded
            # lifecycle transition supplies the historical exemption boundary.
            closure = next((event['seq'] for event in self.connection.execute(
                "SELECT seq, summary FROM events WHERE task = ? AND type = 'status' ORDER BY seq DESC",
                (row['task'],)) if any(event['summary'].startswith(f'{previous} -> {task_status}')
                                      for previous in STATUSES)), None)
            delivery = next((event['seq'] for event in self.connection.execute(
                "SELECT seq, refs FROM events WHERE task = ? AND type IN ('finished', 'reaped') ORDER BY seq DESC",
                (row['task'],)) if row['id'] in json.loads(event['refs'])), None)
            historical = closure is not None and delivery is not None and delivery < closure
        return {
            "id": row["id"],
            "task": row["task"],
            "actor": row["actor"],
            "role": row["role"],
            "mode": row["mode"],
            "scope": json.loads(row["scope"]),
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            # Read-only callers can inspect v1 without migrating it.
            "owner_pid": row["owner_pid"] if "owner_pid" in row.keys() else None,
            "owner_boot_id": row["owner_boot_id"] if "owner_boot_id" in row.keys() else None,
            "owner_host": row["owner_host"] if "owner_host" in row.keys() else None,
            "owner_pid_namespace": row["owner_pid_namespace"] if "owner_pid_namespace" in row.keys() else None,
            "lease_until": row["lease_until"] if "lease_until" in row.keys() else None,
            "result": json.loads(row["result"]) if "result" in row.keys() and row["result"] is not None else None,
            'activity': json.loads(row['activity']) if 'activity' in row.keys() and row['activity'] else None,
            'failure_journal': json.loads(row['failure_journal']) if 'failure_journal' in row.keys() and row['failure_journal'] else None,
            'acknowledged_at': row['acknowledged_at'] if 'acknowledged_at' in row.keys() else None,
            'ack_note': row['ack_note'] if 'ack_note' in row.keys() else '',
            'changes': _changes(row['changes']) if 'changes' in row.keys() and row['changes'] else None,
            'environment': json.loads(row['environment']) if 'environment' in row.keys() and row['environment'] else None,
            'needs': json.loads(row['needs']) if 'needs' in row.keys() and row['needs'] is not None else None,
            'ack_state': ('not_required' if row['status'] == 'active' else 'acknowledged' if acknowledged
                          else 'not_required' if legacy or historical else 'unacknowledged'),
            'usage': json.loads(row['usage']) if 'usage' in row.keys() and row['usage'] is not None else None,
        }

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> TaskEvent:
        return cast(TaskEvent, redact_value({
            "artifact_kind": "hipson.task_event",
            "schema_version": "1.0",
            "seq": row["seq"],
            "task": row["task"],
            "actor": row["actor"],
            "type": row["type"],
            "summary": row["summary"],
            "refs": json.loads(row["refs"]),
            "timestamp": row["timestamp"],
        }))
