"""SQLite session persistence for the future Hipson runtime."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from hipson.redaction import redact_text

SCHEMA_VERSION = 1
MAX_SESSION_JSON_CHARS = 4_000
MAX_SESSION_STRING_CHARS = 1_000
MAX_SESSION_LIST_ITEMS = 20
MAX_SESSION_DICT_KEYS = 30

INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  cwd TEXT NOT NULL,
  repo_root TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
  tool_name TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL,
  approval_status TEXT NOT NULL DEFAULT 'not_required',
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  scope TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  tags_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 1.0,
  approval_status TEXT NOT NULL DEFAULT 'approved',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  skill_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  input_summary TEXT NOT NULL DEFAULT '',
  output_summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'completed',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  schedule TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  run_after TEXT,
  last_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session_created
  ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session_started
  ON tool_calls(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_memories_repo_scope
  ON memories(repo, scope);
CREATE INDEX IF NOT EXISTS idx_jobs_status_run_after
  ON jobs(status, run_after);
"""

CURRENT_SCHEMA_ADDITIONS = """
CREATE TABLE IF NOT EXISTS approval_records (
  id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  tool_call_id TEXT REFERENCES tool_calls(id) ON DELETE SET NULL,
  job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  approved_by TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'tool_call',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approval_records_session_created
  ON approval_records(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_approval_records_tool_call
  ON approval_records(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_approval_records_job
  ON approval_records(job_id);
"""


@dataclass
class SessionStore:
    path: Path
    connection: sqlite3.Connection
    fts_enabled: bool = False

    def __post_init__(self) -> None:
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()
        self.fts_enabled = self._setup_fts()

    def close(self) -> None:
        self.connection.close()

    def create_session(self, cwd: str, repo_root: str | None = None, title: str = "") -> str:
        session_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO sessions (id, title, cwd, repo_root, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, redact_text(title), str(cwd), str(repo_root) if repo_root is not None else None, now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 20) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def session_counts(self, session_id: str) -> dict[str, int]:
        message_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        tool_call_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM tool_calls WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return {
            "messages": int(message_count["count"]) if message_count else 0,
            "tool_calls": int(tool_call_count["count"]) if tool_call_count else 0,
        }

    def add_approval_record(
        self,
        *,
        source: str,
        tool_name: str,
        risk_level: str,
        decision: str,
        reason: str = "",
        session_id: str | None = None,
        tool_call_id: str | None = None,
        job_id: str | None = None,
        approved_by: str = "",
        scope: str = "tool_call",
        metadata: dict[str, object] | None = None,
    ) -> str:
        approval_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO approval_records (
                  id, session_id, tool_call_id, job_id, source, tool_name, risk_level,
                  decision, reason, approved_by, scope, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    session_id,
                    tool_call_id,
                    job_id,
                    redact_text(source),
                    redact_text(tool_name),
                    redact_text(risk_level),
                    redact_text(decision),
                    redact_text(reason),
                    redact_text(approved_by),
                    redact_text(scope),
                    _json_dumps_redacted(metadata or {}),
                    now,
                ),
            )
        return approval_id

    def list_approval_records(
        self,
        *,
        session_id: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if session_id is not None:
            rows = self.connection.execute(
                """
                SELECT * FROM approval_records
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        elif job_id is not None:
            rows = self.connection.execute(
                """
                SELECT * FROM approval_records
                WHERE job_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM approval_records
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_approval_record_row(row) for row in rows]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> str:
        message_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO messages (id, session_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, redact_text(content), _json_dumps_redacted(metadata or {}), now),
            )
            self._touch_session(session_id, now)
        return message_id

    def list_messages(self, session_id: str, limit: int = 100) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [_message_row(row) for row in rows]

    def search_messages(self, query: str, limit: int = 20) -> list[dict[str, object]]:
        clean_query = redact_text(query).strip()
        if not clean_query:
            return []
        pattern = _like_pattern(clean_query)
        rows = self._search_message_rows(clean_query, pattern, limit)
        remaining = max(0, limit - len(rows))
        if remaining:
            rows.extend(self._search_tool_call_rows(pattern, remaining))
        remaining = max(0, limit - len(rows))
        if remaining:
            rows.extend(self._search_memory_rows(clean_query, pattern, remaining))
        return [_search_message_row(row) for row in rows[:limit]]

    def add_memory(
        self,
        *,
        scope: str,
        kind: str,
        summary: str,
        session_id: str | None = None,
        repo: str = "",
        source_refs: list[str] | None = None,
        tags: list[str] | None = None,
        confidence: float = 1.0,
        approval_status: str = "approved",
    ) -> str:
        memory_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO memories (
                  id, session_id, scope, repo, kind, summary, source_refs_json,
                  tags_json, confidence, approval_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    session_id,
                    redact_text(scope),
                    redact_text(repo),
                    redact_text(kind),
                    redact_text(summary),
                    _json_dumps_redacted({"refs": source_refs or []}),
                    _json_dumps_redacted({"tags": tags or []}),
                    confidence,
                    redact_text(approval_status),
                    now,
                    now,
                ),
            )
        return memory_id

    def _search_message_rows(self, clean_query: str, pattern: str, limit: int) -> list[sqlite3.Row]:
        if self.fts_enabled:
            try:
                return self.connection.execute(
                    """
                    SELECT
                      messages.id AS message_id,
                      messages.session_id AS session_id,
                      messages.role AS role,
                      messages.content AS content,
                      messages.created_at AS created_at,
                      sessions.title AS session_title,
                      'message' AS kind,
                      messages.id AS record_id
                    FROM messages_fts
                    JOIN messages ON messages.rowid = messages_fts.rowid
                    JOIN sessions ON sessions.id = messages.session_id
                    WHERE messages_fts MATCH ?
                    ORDER BY messages.created_at DESC
                    LIMIT ?
                    """,
                    (_fts_query(clean_query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        return self.connection.execute(
            """
            SELECT
              messages.id AS message_id,
              messages.session_id AS session_id,
              messages.role AS role,
              messages.content AS content,
              messages.created_at AS created_at,
              sessions.title AS session_title,
              'message' AS kind,
              messages.id AS record_id
            FROM messages
            JOIN sessions ON sessions.id = messages.session_id
            WHERE messages.content LIKE ? ESCAPE '\\'
               OR sessions.title LIKE ? ESCAPE '\\'
            ORDER BY messages.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()

    def _search_tool_call_rows(self, pattern: str, limit: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
              tool_calls.id AS message_id,
              tool_calls.session_id AS session_id,
              'tool' AS role,
              tool_calls.tool_name || ' ' || tool_calls.status || ' ' ||
                tool_calls.approval_status || ' ' || tool_calls.error || ' ' ||
                tool_calls.output_json AS content,
              COALESCE(tool_calls.completed_at, tool_calls.started_at, '') AS created_at,
              sessions.title AS session_title,
              'tool_call' AS kind,
              tool_calls.id AS record_id
            FROM tool_calls
            JOIN sessions ON sessions.id = tool_calls.session_id
            WHERE tool_calls.tool_name LIKE ? ESCAPE '\\'
               OR tool_calls.error LIKE ? ESCAPE '\\'
               OR tool_calls.output_json LIKE ? ESCAPE '\\'
               OR tool_calls.input_json LIKE ? ESCAPE '\\'
            ORDER BY COALESCE(tool_calls.completed_at, tool_calls.started_at, '') DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, limit),
        ).fetchall()

    def _search_memory_rows(self, clean_query: str, pattern: str, limit: int) -> list[sqlite3.Row]:
        if self.fts_enabled:
            try:
                return self.connection.execute(
                    """
                    SELECT
                      memories.id AS message_id,
                      COALESCE(memories.session_id, '') AS session_id,
                      'memory' AS role,
                      memories.summary AS content,
                      memories.created_at AS created_at,
                      memories.kind AS session_title,
                      'memory' AS kind,
                      memories.id AS record_id
                    FROM memories_fts
                    JOIN memories ON memories.rowid = memories_fts.rowid
                    WHERE memories_fts MATCH ?
                    ORDER BY memories.created_at DESC
                    LIMIT ?
                    """,
                    (_fts_query(clean_query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                pass
        return self.connection.execute(
            """
            SELECT
              memories.id AS message_id,
              COALESCE(memories.session_id, '') AS session_id,
              'memory' AS role,
              memories.summary AS content,
              memories.created_at AS created_at,
              memories.kind AS session_title,
              'memory' AS kind,
              memories.id AS record_id
            FROM memories
            WHERE memories.summary LIKE ? ESCAPE '\\'
               OR memories.repo LIKE ? ESCAPE '\\'
               OR memories.kind LIKE ? ESCAPE '\\'
            ORDER BY memories.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        ).fetchall()

    def add_tool_call(
        self,
        session_id: str,
        *,
        tool_name: str,
        input_data: dict[str, object] | None = None,
        output_data: dict[str, object] | None = None,
        message_id: str | None = None,
        risk_level: str = "read",
        approval_status: str = "not_required",
        status: str = "completed",
        error: str = "",
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> str:
        tool_call_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tool_calls (
                  id, session_id, message_id, tool_name, input_json, output_json, risk_level,
                  approval_status, status, error, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    session_id,
                    message_id,
                    tool_name,
                    _json_dumps_redacted(input_data or {}),
                    _json_dumps_redacted(output_data or {}),
                    risk_level,
                    approval_status,
                    status,
                    redact_text(error),
                    started_at or now,
                    completed_at or now,
                ),
            )
            self._touch_session(session_id, now)
        return tool_call_id

    def list_tool_calls(self, session_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM tool_calls
            WHERE session_id = ?
            ORDER BY started_at ASC, completed_at ASC
            """,
            (session_id,),
        ).fetchall()
        return [_tool_call_row(row) for row in rows]

    def add_job(
        self,
        *,
        kind: str,
        payload: dict[str, object] | None = None,
        schedule: str = "",
        status: str = "pending",
        run_after: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        now = timestamp()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO jobs (id, kind, payload_json, schedule, status, run_after, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, kind, _json_dumps_redacted(payload or {}), schedule, status, run_after, now, now),
            )
        return job_id

    def list_jobs(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, object]]:
        if status is None:
            rows = self.connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return [_job_row(row) for row in rows]

    def list_due_jobs(self, *, now: str | None = None, limit: int = 20) -> list[dict[str, object]]:
        effective_now = now or timestamp()
        rows = self.connection.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'pending' AND (run_after IS NULL OR run_after = '' OR run_after <= ?)
            ORDER BY run_after ASC, created_at ASC
            LIMIT ?
            """,
            (effective_now, limit),
        ).fetchall()
        return [_job_row(row) for row in rows]

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        payload: dict[str, object] | None = None,
        run_after: str | None = None,
        last_run_at: str | None = None,
    ) -> None:
        now = timestamp()
        with self.connection:
            if payload is None:
                self.connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, run_after = ?, last_run_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, run_after, last_run_at or now, now, job_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE jobs
                    SET payload_json = ?, status = ?, run_after = ?, last_run_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (_json_dumps_redacted(payload), status, run_after, last_run_at or now, now, job_id),
                )

    def _migrate(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        row = self.connection.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (SCHEMA_VERSION,),
        ).fetchone()
        if row:
            self._ensure_current_schema()
            return
        with self.connection:
            self.connection.executescript(INITIAL_SCHEMA)
            self.connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, timestamp()),
            )
        self._ensure_current_schema()

    def _ensure_current_schema(self) -> None:
        with self.connection:
            self.connection.executescript(CURRENT_SCHEMA_ADDITIONS)

    def _setup_fts(self) -> bool:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(session_id UNINDEXED, content)
                    """
                )
                self.connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                    USING fts5(repo UNINDEXED, scope UNINDEXED, summary)
                    """
                )
                self.connection.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_fts_ai
                    AFTER INSERT ON messages BEGIN
                      INSERT INTO messages_fts(rowid, session_id, content)
                      VALUES (new.rowid, new.session_id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_fts_ad
                    AFTER DELETE ON messages BEGIN
                      DELETE FROM messages_fts WHERE rowid = old.rowid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_fts_au
                    AFTER UPDATE ON messages BEGIN
                      UPDATE messages_fts
                      SET session_id = new.session_id, content = new.content
                      WHERE rowid = new.rowid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS memories_fts_ai
                    AFTER INSERT ON memories BEGIN
                      INSERT INTO memories_fts(rowid, repo, scope, summary)
                      VALUES (new.rowid, new.repo, new.scope, new.summary);
                    END;
                    CREATE TRIGGER IF NOT EXISTS memories_fts_ad
                    AFTER DELETE ON memories BEGIN
                      DELETE FROM memories_fts WHERE rowid = old.rowid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS memories_fts_au
                    AFTER UPDATE ON memories BEGIN
                      UPDATE memories_fts
                      SET repo = new.repo, scope = new.scope, summary = new.summary
                      WHERE rowid = new.rowid;
                    END;
                    """
                )
                self.connection.execute(
                    """
                    INSERT INTO messages_fts(rowid, session_id, content)
                    SELECT messages.rowid, messages.session_id, messages.content
                    FROM messages
                    WHERE NOT EXISTS (
                      SELECT 1 FROM messages_fts WHERE messages_fts.rowid = messages.rowid
                    )
                    """
                )
                self.connection.execute(
                    """
                    INSERT INTO memories_fts(rowid, repo, scope, summary)
                    SELECT memories.rowid, memories.repo, memories.scope, memories.summary
                    FROM memories
                    WHERE NOT EXISTS (
                      SELECT 1 FROM memories_fts WHERE memories_fts.rowid = memories.rowid
                    )
                    """
                )
        except sqlite3.OperationalError:
            return False
        return True

    def _touch_session(self, session_id: str, updated_at: str) -> None:
        self.connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (updated_at, session_id))


def open_session_store(path: str | Path) -> SessionStore:
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SessionStore(path=db_path, connection=sqlite3.connect(db_path))


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_dumps_redacted(value: dict[str, object]) -> str:
    bounded = _bound_session_value(value)
    encoded = redact_text(json.dumps(bounded, ensure_ascii=False, sort_keys=True))
    if len(encoded) <= MAX_SESSION_JSON_CHARS:
        return encoded
    fallback = {
        "summary": "Session JSON omitted after bounding",
        "truncated": True,
    }
    return json.dumps(fallback, ensure_ascii=False, sort_keys=True)


def _bound_session_value(value: object) -> object:
    if isinstance(value, str):
        return _bound_session_string(value)
    if isinstance(value, list):
        list_items = [_bound_session_value(item) for item in value[:MAX_SESSION_LIST_ITEMS]]
        if len(value) > MAX_SESSION_LIST_ITEMS:
            list_items.append({"truncated_items": len(value) - MAX_SESSION_LIST_ITEMS})
        return list_items
    if isinstance(value, dict):
        output: dict[str, object] = {}
        dict_items = list(cast(dict[object, object], value).items())
        for key, item in dict_items[:MAX_SESSION_DICT_KEYS]:
            output[str(key)] = _bound_session_value(item)
        if len(dict_items) > MAX_SESSION_DICT_KEYS:
            output["_truncated_keys"] = len(dict_items) - MAX_SESSION_DICT_KEYS
        return output
    return value


def _bound_session_string(value: str) -> str:
    redacted = redact_text(value)
    if len(redacted) <= MAX_SESSION_STRING_CHARS:
        return redacted
    marker = f"... [truncated to {MAX_SESSION_STRING_CHARS} chars]"
    return redacted[: max(0, MAX_SESSION_STRING_CHARS - len(marker))].rstrip() + marker


def _json_loads_object(value: str) -> dict[str, object]:
    data = json.loads(value)
    return data if isinstance(data, dict) else {}


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_query(value: str) -> str:
    escaped = value.replace('"', '""').strip()
    return f'"{escaped}"'


def _message_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["metadata"] = _json_loads_object(str(data.pop("metadata_json")))
    return data


def _tool_call_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["input"] = _json_loads_object(str(data.pop("input_json")))
    data["output"] = _json_loads_object(str(data.pop("output_json")))
    return data


def _search_message_row(row: sqlite3.Row) -> dict[str, object]:
    return dict(row)


def _approval_record_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["metadata"] = _json_loads_object(str(data.pop("metadata_json")))
    return data


def _job_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    data["payload"] = _json_loads_object(str(data.pop("payload_json")))
    return data
