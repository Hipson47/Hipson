"""Transactional task ledger written by the lead on behalf of workers.

Actor names are recorded labels, not authenticated identities. Path claims are
lexical coordination records; they do not enforce filesystem access.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import sqlite3
import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypedDict

from hipson.home import detect_hipson_home
from hipson.redaction import redact_text

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 10_000
SQLITE_MAX_INTEGER = 2**63 - 1
STATUSES = ("open", "in_progress", "blocked", "review", "done", "cancelled")
ROLES = ("builder", "reviewer", "explorer", "researcher")
MODES = ("ro", "rw")
RESULTS = ("passed", "failed", "cancelled")
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
SCHEMA = (
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
        revision INTEGER NOT NULL CHECK (revision >= 0)
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


class Task(TypedDict):
    artifact_kind: str
    schema_version: str
    id: str
    project: str
    title: str
    objective: str
    acceptance: list[str]
    status: str
    lead: str
    created_at: str
    updated_at: str
    revision: int


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
    lease_until: str | None


class ReapedAssignment(TypedDict):
    assignment: Assignment
    reason: str


class Claim(TypedDict):
    assignment: str
    path: str


class TaskView(Task):
    assignments: list[Assignment]
    claims: list[Claim]


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


def _required(value: str, field: str) -> str:
    if not value.strip():
        raise LedgerError(f"{field} must not be empty")
    return value.strip()


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() or None
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    """The owner PID and its worker process group must both be absent to reap."""
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


def _wal_read_only_connection(path: Path) -> sqlite3.Connection | None:
    """Pin existing WAL sidecars until SQLite holds its own database read lock."""
    try:
        import fcntl

        lock_command = fcntl.F_OFD_SETLK
    except (ImportError, AttributeError) as exc:
        raise LedgerError("Reading live task ledger WAL without creating files requires Linux OFD locks") from exc
    # SQLite's SHARED lock range prevents last-writer cleanup of WAL/SHM.
    # OFD locks are independent of SQLite's process locks: closing this guard
    # cannot release locks held by another Ledger connection in this process.
    with path.open("rb") as guard:
        lock = struct.pack("hhqqi4x", fcntl.F_RDLCK, os.SEEK_SET, 0x40000002, 510, 0)
        try:
            fcntl.fcntl(guard.fileno(), lock_command, lock)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                return None
            raise LedgerError("Cannot guard read-only task ledger WAL; Linux OFD locking is required") from exc
        if not Path(f"{path}-wal").exists():
            return None  # A writer closed before the guard; retry as a snapshot.
        if not Path(f"{path}-shm").exists():
            raise LedgerError(
                "Cannot read task ledger WAL without a readable -shm file; "
                "have a writable owner initialize the WAL sidecars, or checkpoint and close the store. "
                "The read was not retried writable."
            )
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro&readonly_shm=1", uri=True,
            timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
        )
        try:
            connection.execute("SELECT version FROM schema_version WHERE singleton = 1").fetchone()
        except BaseException:
            connection.close()
            raise
        return connection


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """Avoid creating WAL sidecars when reading a checkpointed database."""
    if path.exists() and not path.is_file():
        raise sqlite3.OperationalError("unable to open database file")
    wal = Path(f"{path}-wal")
    journal = Path(f"{path}-journal")
    for _ in range(3):
        if wal.exists():
            connection = _wal_read_only_connection(path)
            if connection is not None:
                return connection
            continue
        if journal.exists():
            raise LedgerError("Task ledger has a rollback journal; retry after its writable owner completes recovery")
        # immutable is confined to copying a journal-free file into memory, never
        # a live WAL view. Validate the source again before exposing the snapshot.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as source:
            before = path.stat()
            snapshot = sqlite3.connect(":memory:", isolation_level=None)
            try:
                source.backup(snapshot)
                after = path.stat()
                identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
                if (
                    all(getattr(before, key) == getattr(after, key) for key in identity)
                    and not wal.exists() and not journal.exists()
                ):
                    snapshot.execute("PRAGMA query_only = ON")
                    return snapshot
            except BaseException:
                snapshot.close()
                raise
            snapshot.close()
    raise LedgerError("Task ledger changed while opening a read-only snapshot; retry the read")


class Ledger:
    """One SQLite connection; callers must close it (or use contextlib.closing)."""

    def __init__(
        self,
        db: str | Path | None = None,
        *,
        create: bool = True,
        read_only: bool = False,
        hostname: Callable[[], str] = socket.gethostname,
        boot_id: Callable[[], str | None] = _boot_id,
        pid_alive: Callable[[int], bool] = _pid_alive,
    ) -> None:
        self.path = Path(db).expanduser().resolve() if db is not None else default_ledger_db()
        self._hostname = hostname
        self._boot_id = boot_id
        self._pid_alive = pid_alive
        if create and not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # mode=rw refuses to create a missing file, even if it disappears after a caller's existence check.
        target = self.path if create else self.path.as_uri() + "?mode=rw"
        self.connection = _read_only_connection(self.path) if read_only else sqlite3.connect(
            target, uri=not create, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
        )
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
                elif version is not None and version[0] == 1:
                    if not read_only:
                        for statement in MIGRATION_V1_TO_V2:
                            self.connection.execute(statement)
                elif version is None or version[0] != SCHEMA_VERSION:
                    found = version[0] if version is not None else None
                    raise LedgerError(f"Unsupported task ledger schema version {found}; expected {SCHEMA_VERSION}")
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
    ) -> TaskView:
        project_path = str(Path(project).expanduser().resolve())
        title = redact_text(_required(title, "title"))
        objective = redact_text(_required(objective, "objective"))
        lead = _required(lead, "lead")
        accepted = [redact_text(item) for item in acceptance]
        with self._transaction():
            number = self.connection.execute(
                "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 FROM tasks"
            ).fetchone()[0]
            task = f"T-{number:04d}"
            now = _timestamp()
            self.connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, 0)",
                (task, project_path, title, objective, json.dumps(accepted), lead, now, now),
            )
            self._append_event(task, lead, "opened", title, [])
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
        lease_seconds: int | None = None,
    ) -> Assignment:
        actor = _required(actor, "actor")
        if role not in ROLES or mode not in MODES:
            raise LedgerError(f"Assignment requires role in {ROLES} and mode in {MODES}")
        paths = normalize_scope(scope)
        if mode == "rw" and not paths:
            # An rw assignment without claims would let two writers share a tree unnoticed.
            raise LedgerError("An rw assignment needs at least one scope path; use '.' for the whole project")
        if owner_pid is not None and (type(owner_pid) is not int or not 1 <= owner_pid <= 2**31 - 1):
            raise LedgerError("owner_pid must be an integer between 1 and 2147483647")
        owner_boot_id = _required(owner_boot_id, "owner_boot_id") if owner_boot_id is not None else None
        owner_host = _required(owner_host, "owner_host") if owner_host is not None else None
        if lease_seconds is not None and (type(lease_seconds) is not int or lease_seconds < 0):
            raise LedgerError("lease_seconds must be a nonnegative integer")
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
                        owner_pid, owner_boot_id, owner_host, lease_until)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?)""",
                    (assignment, task, actor, role, mode, json.dumps(paths), now,
                     owner_pid, owner_boot_id, owner_host, lease_until),
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
            raise ClaimConflictError(conflict)
        return result

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
                    result.append({"assignment": item, "reason": reason})
        return result

    def _orphan_reason(self, item: Assignment) -> str | None:
        if item["lease_until"] is not None and datetime.fromisoformat(item["lease_until"]) <= datetime.now(UTC):
            return "lease_expired"
        if item["owner_host"] is None or item["owner_host"] != self._hostname():
            return None
        if item["owner_boot_id"] is not None:
            boot = self._boot_id()
            if boot is not None and item["owner_boot_id"] != boot:
                return "boot_id_changed"
        if item["owner_pid"] is not None and not self._pid_alive(item["owner_pid"]):
            return "pid_gone"
        return None

    def _reap_assignment(self, item: Assignment, reason: str) -> Assignment:
        self.connection.execute(
            "UPDATE assignments SET status = 'cancelled', ended_at = ? WHERE id = ?", (_timestamp(), item["id"])
        )
        self.connection.execute("DELETE FROM claims WHERE assignment = ?", (item["id"],))
        self._append_event(item["task"], item["actor"], "reaped", f"{item['id']}: {reason}", [item["id"]])
        return self._assignment(item["id"])

    def finish(self, assignment: str, *, result: str, summary: str, refs: Sequence[str] = ()) -> Assignment:
        if result not in RESULTS:
            raise LedgerError(f"Assignment result must be one of {RESULTS}")
        summary = _required(summary, "summary")
        with self._transaction():
            current = self._assignment(assignment)
            if current["status"] != "active":
                raise LedgerError(f"Assignment {assignment} is already finished ({current['status']})")
            self.connection.execute(
                "UPDATE assignments SET status = ?, ended_at = ? WHERE id = ?", (result, _timestamp(), assignment)
            )
            self.connection.execute("DELETE FROM claims WHERE assignment = ?", (assignment,))
            self._append_event(
                current["task"], current["actor"], "finished", f"{result}: {summary}", [assignment, *refs]
            )
            return self._assignment(assignment)

    def add_event(self, task: str, *, actor: str, type: str, summary: str, refs: Sequence[str] = ()) -> TaskEvent:
        actor = _required(actor, "actor")
        type = _required(type, "type")
        summary = _required(summary, "summary")
        with self._transaction():
            self._task(task)
            return self._append_event(task, actor, type, summary, refs)

    def set_status(self, task: str, *, to: str, actor: str, summary: str = "") -> TaskView:
        actor = _required(actor, "actor")
        with self._transaction():
            current = self._task(task)
            previous = current["status"]
            if to not in TRANSITIONS[previous]:
                raise LedgerError(f"Invalid task transition for {task}: {previous} -> {to}")
            self.connection.execute("UPDATE tasks SET status = ? WHERE id = ?", (to, task))
            self._append_event(task, actor, "status", summary or f"{previous} -> {to}", [])
            return self._view(task)

    def show(self, task: str) -> TaskView:
        with self._transaction(write=False):
            return self._view(task)

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
        return [
            self._task_dict(row)
            for row in self.connection.execute(
                """SELECT * FROM tasks WHERE (? IS NULL OR project = ?) AND (? IS NULL OR status = ?)
                   ORDER BY created_at, id""",
                (project_path, project_path, status, status),
            )
        ]

    def board(self) -> list[TaskView]:
        with self._transaction(write=False):
            tasks = self.connection.execute(
                "SELECT id FROM tasks WHERE status NOT IN ('done', 'cancelled') ORDER BY created_at, id"
            ).fetchall()
            return [self._view(row["id"], active_only=True) for row in tasks]

    def _task(self, task: str) -> Task:
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task,)).fetchone()
        if row is None:
            raise LedgerError(f"Task not found: {task}")
        return self._task_dict(row)

    def _assignment(self, assignment: str) -> Assignment:
        row = self.connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment,)).fetchone()
        if row is None:
            raise LedgerError(f"Assignment not found: {assignment}")
        return self._assignment_dict(row)

    def _view(self, task: str, *, active_only: bool = False) -> TaskView:
        current = self._task(task)
        assignments = [
            self._assignment_dict(row)
            for row in self.connection.execute(
                "SELECT * FROM assignments WHERE task = ? AND (? = 0 OR status = 'active') ORDER BY started_at, id",
                (task, active_only),
            )
        ]
        claims: list[Claim] = [
            {"assignment": row["assignment"], "path": row["path"]}
            for row in self.connection.execute(
                """SELECT c.* FROM claims c JOIN assignments a ON a.id = c.assignment
                   WHERE a.task = ? ORDER BY c.assignment, c.path""",
                (task,),
            )
        ]
        return {**current, "assignments": assignments, "claims": claims}

    def _append_event(self, task: str, actor: str, type: str, summary: str, refs: Sequence[str]) -> TaskEvent:
        now = _timestamp()
        cursor = self.connection.execute(
            "INSERT INTO events (task, actor, type, summary, refs, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (task, actor, type, redact_text(summary), json.dumps(list(refs)), now),
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
            "status": row["status"],
            "lead": row["lead"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
        }

    @staticmethod
    def _assignment_dict(row: sqlite3.Row) -> Assignment:
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
            "lease_until": row["lease_until"] if "lease_until" in row.keys() else None,
        }

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> TaskEvent:
        return {
            "artifact_kind": "hipson.task_event",
            "schema_version": "1.0",
            "seq": row["seq"],
            "task": row["task"],
            "actor": row["actor"],
            "type": row["type"],
            "summary": row["summary"],
            "refs": json.loads(row["refs"]),
            "timestamp": row["timestamp"],
        }
