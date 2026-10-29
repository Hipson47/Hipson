"""Transactional task ledger written by the lead on behalf of workers.

Actor names are recorded labels, not authenticated identities. Path claims are
lexical coordination records; they do not enforce filesystem access.
"""

from __future__ import annotations

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

SCHEMA_VERSION = 10
BUSY_TIMEOUT_MS = 10_000
SQLITE_MAX_INTEGER = 2**63 - 1
STATUSES = ("open", "in_progress", "blocked", "review", "done", "cancelled")
ROLES = ("builder", "reviewer", "explorer", "researcher")
MODES = ("ro", "rw")
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
        needs TEXT NOT NULL DEFAULT '[]'
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


class CloseRefusedError(LedgerError):
    """A close was refused before any transition; reasons name the command that clears each blocker."""

    def __init__(self, task: str, reasons: list[str]) -> None:
        super().__init__(f"Cannot close {task}: " + "; ".join(reasons))
        self.reasons = reasons


RESERVED_EVENT_TYPES = frozenset({
    "opened", "assigned", "status", "finished", "reaped", "acknowledged", "lead_verified", "checks_set",
    "claim_conflict", "reopened", "override", "observed", "routed",
})


def reserved_event_type(type: str) -> bool:
    """Lifecycle readers trust these types; free-form events must not forge them."""
    return (type in RESERVED_EVENT_TYPES or type.startswith("finding_")
            or (type.endswith("_override") and type != "project_mismatch_override"))


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
    return blockers


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
            raise LedgerError("Scope must contain relative repository paths without '..' or drive prefixes")
        # Stripping would silently rename a path ('src ' -> 'src') and hide overlaps; refuse edge whitespace instead.
        if any(part != part.strip() for part in path.parts):
            raise LedgerError(f"Scope path components must not start or end with whitespace: {raw!r}")
        normalized.append(path.as_posix())
    return list(dict.fromkeys(normalized))


def _overlaps(left: str, right: str) -> bool:
    return left == "." or right == "." or left == right or left.startswith(right + "/") or right.startswith(left + "/")


def validate_assignment_scope(scope: Sequence[str], *, mode: str) -> list[str]:
    """Validate claims before opening a store or starting an assignment transaction."""
    paths = normalize_scope(scope)
    if mode == "rw" and not paths:
        # An rw assignment without claims would let two writers share a tree unnoticed.
        raise LedgerError("An rw assignment needs at least one scope path; use '.' for the whole project")
    if mode == "rw" and any(redact_text(path) != path for path in paths):
        # Redacting a claim would silently change the path it protects.
        raise LedgerError("Write scope paths must not contain secret-shaped text")
    return [redact_text(path) for path in paths]


def validate_status_override(*, to: str, force: bool, reason: str) -> None:
    if type(force) is not bool or not isinstance(reason, str):
        raise LedgerError("force must be boolean and reason must be text")
    if force:
        require_text(reason, "Override reason")
        if to not in ("review", "done"):
            raise LedgerError("--force and --reason apply only to review or done")
    elif reason:
        raise LedgerError("--reason requires --force")


def require_text(value: str, field: str) -> str:
    """Require nonblank text at both the ledger and transport boundaries."""
    if not value.strip():
        raise LedgerError(f"{field} must not be empty")
    if _has_surrogate(value):
        raise LedgerError(f"{field} must be valid Unicode text (lone surrogate)")
    return value.strip()


def _has_surrogate(value: str) -> bool:
    # Lone surrogates survive json.dumps(ensure_ascii=True) but break every UTF-8 writer later.
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def validate_checks(checks: Sequence[Sequence[str]]) -> list[list[str]]:
    """Acceptance checks are exact argv lists run by the lead without a shell."""
    if isinstance(checks, str) or len(checks) > MAX_CHECKS:
        raise LedgerError(f"Acceptance checks must be a list of at most {MAX_CHECKS} argv lists")
    normalized: list[list[str]] = []
    for check in checks:
        if isinstance(check, str) or not isinstance(check, Sequence) or not check or len(check) > MAX_CHECK_ARGS:
            raise LedgerError(f"Each acceptance check must be a nonempty argv list of at most {MAX_CHECK_ARGS} items")
        argv = list(check)
        for arg in argv:
            if (not isinstance(arg, str) or len(arg) > CHECK_ARG_LIMIT
                    or any(ord(char) < 32 or ord(char) == 127 for char in arg)):
                raise LedgerError(f"Acceptance check arguments must be strings of at most {CHECK_ARG_LIMIT} "
                                  "characters without control characters")
            if redact_text(arg) != arg:
                raise LedgerError("Acceptance checks must not contain secret-shaped text; pass secrets via the environment")
        if not argv[0].strip():
            raise LedgerError("An acceptance check needs a command name")
        if argv not in normalized:
            normalized.append(argv)
    return normalized


def checks_text(checks: Sequence[Sequence[str]]) -> str:
    return '; '.join(shlex.join(check) for check in checks) or 'none declared'


def validate_needs(needs: Sequence[str]) -> list[str]:
    """Capability tags a task or run needs (e.g. code_write, web_research): lower-case slugs, deduplicated."""
    if isinstance(needs, str) or len(needs) > MAX_NEEDS:
        raise LedgerError(f"Capability tags must be a list of at most {MAX_NEEDS} names")
    tags: list[str] = []
    for tag in needs:
        if not isinstance(tag, str) or not NEED_PATTERN.fullmatch(tag):
            raise LedgerError(f"Capability tags are lower-case names such as code_write, not {tag!r}")
        if tag not in tags:
            tags.append(tag)
    return tags


def _validate_result_value(value: Any, schema: dict[str, Any], path: str) -> None:
    """Validate the fixed result contract's keywords, without a runtime dependency."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    if not any(type(value) is types[name] for name in names):
        raise LedgerError(f"Invalid worker result at {path}: expected {' or '.join(names)}")
    if "enum" in schema and value not in schema["enum"]:
        raise LedgerError(f"Invalid worker result at {path}: expected one of {schema['enum']}")
    if isinstance(value, str) and "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise LedgerError(f"Invalid worker result at {path}: must not be empty or blank")
    if isinstance(value, str) and _has_surrogate(value):
        raise LedgerError(f"Invalid worker result at {path}: lone surrogate is not valid Unicode text")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) - properties.keys():
            raise LedgerError(f"Invalid worker result at {path}: unknown properties")
        for key in schema["required"]:
            if key not in value:
                raise LedgerError(f"Invalid worker result at {path}.{key}: required property missing")
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
                raise LedgerError("Invalid worker result JSON: duplicate object property")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise LedgerError("Invalid worker result JSON: non-finite number")

    try:
        # Bound the allocation even if a worker grows/replaces the file while read.
        with Path(path).expanduser().open("rb") as stream:
            data = stream.read(2**20 + 1)
        if len(data) > 2**20:
            raise LedgerError("Invalid worker result JSON: exceeds 1 MiB limit")
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object, parse_constant=invalid_constant,
        )
        return validate_task_result(value)
    except LedgerError:
        raise
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Invalid worker result JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})") from exc
    except UnicodeError as exc:
        raise LedgerError("Invalid worker result JSON: expected UTF-8") from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        raise LedgerError("Invalid worker result JSON: parser resource limit") from exc


def _changes(raw: str) -> dict[str, Any]:
    """Change observations recorded before incomplete_reasons existed still read and render."""
    record = json.loads(raw)
    if isinstance(record, dict):
        record.setdefault("incomplete_reasons", [])
    return record


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        raise sqlite3.OperationalError("unable to open database file")
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
            raise LedgerError(message)

    while time.monotonic() < deadline:
        if journal.exists():
            raise LedgerError("Task ledger has a rollback journal; retry after its writable owner completes recovery")
        wal_before = wal.stat() if sidecar_free and wal.exists() else None
        if wal_before is not None and wal_before.st_size:
            raise LedgerError('Task ledger has a nonempty WAL; retry after its writable owner completes recovery '
                              'and checkpoints/closes the store (sidecar-free read refused)')
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
    raise LedgerError(message)


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
            raise LedgerError('A sidecar-free connection requires read_only=True')
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
            with self._transaction(write=not read_only):
                if not read_only:
                    self.connection.execute(SCHEMA[0])
                version = self.connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
                if version is None and not read_only:
                    for statement in SCHEMA[1:]:
                        self.connection.execute(statement)
                    self.connection.execute("INSERT INTO schema_version VALUES (1, ?)", (SCHEMA_VERSION,))
                elif version is None or version[0] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, SCHEMA_VERSION):
                    found = version[0] if version is not None else None
                    raise LedgerError(f"Unsupported task ledger schema version {found}; expected {SCHEMA_VERSION}")
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
    ) -> TaskView:
        project_path = str(Path(project).expanduser().resolve())
        if redact_text(project_path) != project_path:
            raise LedgerError("Project paths must not contain secret-shaped text")
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
                " revision, checks, needs) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 0, ?, ?)",
                (task, project_path, title, objective, json.dumps(accepted), lead, now, now, json.dumps(declared),
                 json.dumps(tags)),
            )
            self._append_event(task, lead, "opened", title, [])
            if declared:
                self._append_event(task, lead, "checks_set", "acceptance checks: " + checks_text(declared), [])
            return self._view(task)

    def set_checks(self, task: str, *, checks: Sequence[Sequence[str]], actor: str, reason: str = "") -> TaskView:
        """Replace the declared acceptance checks; closure needs each one to pass fresh."""
        actor = redact_text(require_text(actor, "actor"))
        declared = validate_checks(checks)
        with self._transaction():
            current = self._task(task)
            if current["status"] in ("done", "cancelled"):
                raise LedgerError(f"Cannot change acceptance checks of {current['status']} task {task}")
            if current["checks"] and not reason.strip():
                raise LedgerError(f"Replacing declared acceptance checks of {task} requires --reason")
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
            raise LedgerError(f"Assignment requires role in {ROLES} and mode in {MODES}")
        paths = validate_assignment_scope(scope, mode=mode)
        if owner_pid is not None and (type(owner_pid) is not int or not 1 <= owner_pid <= 2**31 - 1):
            raise LedgerError("owner_pid must be an integer between 1 and 2147483647")
        owner_boot_id = redact_text(require_text(owner_boot_id, "owner_boot_id")) if owner_boot_id is not None else None
        owner_host = redact_text(require_text(owner_host, "owner_host")) if owner_host is not None else None
        if owner_pid_namespace is not None and not isinstance(owner_pid_namespace, str):
            raise LedgerError("owner_pid_namespace must be a string")
        owner_pid_namespace = redact_text(require_text(owner_pid_namespace, "owner_pid_namespace")) if owner_pid_namespace is not None else None
        if lease_seconds is not None and (type(lease_seconds) is not int or lease_seconds < 0):
            raise LedgerError("lease_seconds must be a nonnegative integer")
        if environment is not None and (not isinstance(environment, dict)
                                        or environment.get("mode") not in ("inherit", "minimal")):
            raise LedgerError("environment must be an object with mode inherit or minimal")
        tags = validate_needs(needs) if needs is not None else None
        result: Assignment | None = None
        conflict = ""
        with self._transaction():
            current = self._task(task)
            if current["status"] in ("done", "cancelled"):
                raise LedgerError(f"Cannot assign to terminal task {task} ({current['status']})")
            now = _timestamp()
            try:
                lease_until = (
                    (datetime.fromisoformat(now) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
                    if lease_seconds is not None else None
                )
            except OverflowError as exc:
                raise LedgerError("lease_seconds is too large") from exc
            if mode == "rw":
                holders = self.connection.execute(
                    """SELECT c.assignment, c.path, a.* FROM claims c
                       JOIN assignments a ON a.id = c.assignment
                       JOIN tasks t ON t.id = a.task
                       WHERE t.project = ? AND a.status = 'active'
                       ORDER BY c.assignment, c.path""",
                    (current["project"],),
                ).fetchall()
                reaped: set[str] = set()
                checked: set[str] = set()
                for holder in holders:
                    if holder["id"] in checked or not any(_overlaps(path, holder["path"]) for path in paths):
                        continue
                    checked.add(holder["id"])
                    item = self._assignment_dict(holder)
                    reason = self._orphan_reason(item)
                    if reason is not None:
                        self._reap_assignment(item, reason)
                        reaped.add(item["id"])
                holders = [holder for holder in holders if holder["id"] not in reaped]
                for path in paths:
                    holder = next((row for row in holders if _overlaps(path, row["path"])), None)
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
                                     f"{holder['assignment']} and wait, or run hipson task reap if its owner died")
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
            "UPDATE assignments SET status = 'cancelled', ended_at = ?, failure_journal = ? WHERE id = ?",
            (now, json.dumps(journal), item["id"]),
        )
        self.connection.execute("DELETE FROM claims WHERE assignment = ?", (item["id"],))
        self._append_event(item["task"], item["actor"], "reaped", f"{item['id']}: {journal['reason']}", [item["id"]])
        return self._assignment(item["id"])

    def record_changes(self, assignment: str, changes: dict[str, Any],
                       findings: Sequence[dict[str, str]] = ()) -> Assignment:
        """Record a post-run change observation once, with its findings and an 'observed' event, atomically."""
        if not isinstance(changes, dict) or changes.get("status") not in ("clean", "changes", "incomplete", "unobserved"):
            raise LedgerError("Change observation must be an object with a known status")
        with self._transaction():
            current = self._assignment(assignment)
            if current["status"] == "active":
                raise LedgerError(f"Assignment {assignment} is still active")
            if current["changes"] is not None:
                raise LedgerError(f"Assignment {assignment} already has a change observation")
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
                raise LedgerError(f'Assignment {assignment} is already finished')
            self.connection.execute('UPDATE assignments SET activity = ? WHERE id = ?',
                                    (json.dumps(snapshot), assignment))

    def activity(self, assignment: str) -> Assignment:
        with self._transaction(write=False):
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def ack(self, assignment: str, *, note: str = '') -> Assignment:
        """Explicit, idempotent lead acknowledgement; never inferred from reads."""
        with self._transaction():
            current = self._assignment(assignment)
            if current['status'] == 'active':
                raise LedgerError(f'Cannot acknowledge active assignment {assignment}')
            if current['acknowledged_at'] is None:
                from hipson.task_activity import safe_text
                self.connection.execute('UPDATE assignments SET acknowledged_at = ?, ack_note = ? WHERE id = ?',
                                        (_timestamp(), safe_text(note), assignment))
                self._append_event(current['task'], self._task(current['task'])['lead'], 'acknowledged',
                                   f'{assignment}: {safe_text(note) or "result acknowledged"}', [assignment])
            return cast(Assignment, redact_value(self._assignment(assignment)))

    def finish(
        self, assignment: str, *, result: str, summary: str, refs: Sequence[str] = (),
        worker_result: object = None,
        failure_journal: dict[str, Any] | None = None,
    ) -> Assignment:
        if result not in RESULTS:
            raise LedgerError(f"Assignment result must be one of {RESULTS}")
        summary = require_text(summary, "summary")
        report = validate_task_result(worker_result) if worker_result is not None else None
        from hipson.task_activity import bounded_journal
        journal = bounded_journal(failure_journal) if failure_journal is not None else None
        with self._transaction():
            current = self._assignment(assignment)
            if current["status"] != "active":
                raise LedgerError(f"Assignment {assignment} is already finished ({current['status']})")
            self.connection.execute(
                "UPDATE assignments SET status = ?, ended_at = ?, result = ?, failure_journal = ? WHERE id = ?",
                (result, _timestamp(), json.dumps(report) if report is not None else None,
                 json.dumps(journal) if journal is not None else None, assignment),
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
            raise LedgerError(f"Finding severity must be one of {FINDING_SEVERITIES}")
        with self._transaction():
            self._task(task)
            self._check_finding_assignment(task, source_assignment)
            return self._insert_finding(
                task, actor=actor, severity=severity, title=title, detail=detail, location=location,
                source_assignment=source_assignment,
            )

    def _check_finding_assignment(self, task: str, assignment: str | None) -> None:
        if assignment is not None and self._assignment(assignment)["task"] != task:
            raise LedgerError(f"Assignment {assignment} does not belong to task {task}")

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
            raise LedgerError(f"Finding status must be one of {FINDING_STATUSES}")
        with self._transaction():
            current = self._finding(finding)
            previous = current["status"]
            if previous == status or (previous != "open" and status != "open"):
                raise LedgerError(f"Invalid finding transition for {finding}: {previous} -> {status}")
            if status == "open":
                resolution_note = require_text(resolution_note, "Reopening note")
                if resolved_by_assignment is not None:
                    raise LedgerError("A reopened finding cannot have a resolved-by assignment")
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
            raise LedgerError(f"Finding status must be one of {FINDING_STATUSES}")
        if severity is not None and severity not in FINDING_SEVERITIES:
            raise LedgerError(f"Finding severity must be one of {FINDING_SEVERITIES}")
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
            raise LedgerError(f"Finding not found: {finding}")
        return cast(Finding, dict(row))

    def add_event(self, task: str, *, actor: str, type: str, summary: str, refs: Sequence[str] = ()) -> TaskEvent:
        actor = require_text(actor, "actor")
        type = require_text(type, "type")
        summary = require_text(summary, "summary")
        if reserved_event_type(type):
            raise LedgerError(f"Event type {type!r} is reserved for ledger-recorded transitions; "
                              "use a descriptive type such as progress or note")
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

    def set_status(
        self, task: str, *, to: str, actor: str, summary: str = "", force: bool = False, reason: str = "",
    ) -> TaskView:
        actor = require_text(actor, "actor")
        validate_status_override(to=to, force=force, reason=reason)
        with self._transaction():
            return self._set_status_locked(task, to=to, actor=actor, summary=summary, force=force, reason=reason)

    def close_task(self, task: str, *, to: str = "done", actor: str, summary: str = "") -> TaskView:
        """Move through review (and done) in one transaction; any refused gate rolls back every step."""
        actor = require_text(actor, "actor")
        if to not in ("review", "done"):
            raise LedgerError("Close target must be review or done")
        with self._transaction():
            view = self._view(task)
            status = view["status"]
            if status not in ("open", "in_progress", "review") or (status == "review" and to == "review"):
                raise CloseRefusedError(task, [f"{task} is {status}; close works from open, in_progress or review"
                                               + ("" if status != "review" else " (use --to done)")])
            reasons = [] if view["verification"]["passing"] else list(view["verification"]["reasons"])
            blockers = done_blockers(view)
            # Review must not race a writer assigned after the caller's own checks either.
            reasons += blockers if to == "done" else [item for item in blockers if item.startswith("active assignment")]
            if reasons:
                raise CloseRefusedError(task, reasons)
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
            raise LedgerError(f"Invalid task transition for {task}: {previous} -> {to} (allowed: {allowed}){hint}")
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
                raise LedgerError(f"Cannot mark {task} {to}: " + "; ".join(blockers) + hint)
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
                raise LedgerError(f"Cannot mark {task} {to}: " + '; '.join(final['verification']['reasons']))
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
            raise LedgerError(f"Event cursor --after must be between 0 and {SQLITE_MAX_INTEGER}")
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
            raise LedgerError(f"Task status must be one of {STATUSES}")
        project_path = str(Path(project).expanduser().resolve()) if project is not None else None
        return cast(list[Task], redact_value([
            self._task_dict(row)
            for row in self.connection.execute(
                """SELECT * FROM tasks WHERE (? IS NULL OR project = ?) AND (? IS NULL OR status = ?)
                   ORDER BY created_at, id""",
                (project_path, project_path, status, status),
            )
        ]))

    def board(self) -> list[TaskView]:
        with self._transaction(write=False):
            views = [self._view(row['id'], active_only=True) for row in self.connection.execute(
                'SELECT id FROM tasks ORDER BY created_at, id')]
            return [view for view in views if view['status'] not in ('done', 'cancelled') or view['assignments']]

    def lead_snapshot(self, *, project: str | Path | None = None) -> list[tuple[TaskView, list[TaskEvent]]]:
        """Read views and events at one ledger revision, without migration or recovery."""
        project_path = str(Path(project).expanduser().resolve()) if project is not None else None
        with self._transaction(write=False):
            return [
                (self._view(row['id']), [self._event_dict(event) for event in self.connection.execute(
                    'SELECT * FROM events WHERE task = ? ORDER BY seq', (row['id'],))])
                for row in self.connection.execute(
                    'SELECT id FROM tasks WHERE (? IS NULL OR project = ?) ORDER BY id',
                    (project_path, project_path),
                )
            ]

    def _task(self, task: str) -> Task:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task,)).fetchone()
        if row is None:
            raise LedgerError(f"Task not found: {task} (list tasks with hipson task list or hipson task now --all)")
        return self._task_dict(row)

    def _assignment(self, assignment: str) -> Assignment:
        row = self.connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment,)).fetchone()
        if row is None:
            raise LedgerError(f"Assignment not found: {assignment}")
        return self._assignment_dict(row)

    def _view(self, task: str, *, active_only: bool = False) -> TaskView:
        current = self._task(task)
        from hipson.task_verification import project_state, verification_state
        records = self.verifications(task)
        verification = verification_state(records, project_state(current['project'], self.path),
                                          self._builder_finished_seq(task), current['lead'], current['checks'])
        assignments = [
            self._assignment_dict(row)
            for row in self.connection.execute(
                "SELECT * FROM assignments WHERE task = ? ORDER BY started_at, id",
                (task,),
            )
        ]
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
                                          "verification": verification}))

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
