-- Original v1 DDL from ledger.py at 9dbd09f; do not derive from the current schema.
CREATE TABLE IF NOT EXISTS schema_version (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        version INTEGER NOT NULL
    );
CREATE TABLE tasks (
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
    );
CREATE TABLE assignments (
        id TEXT PRIMARY KEY,
        task TEXT NOT NULL REFERENCES tasks(id),
        actor TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('builder', 'reviewer', 'explorer', 'researcher')),
        mode TEXT NOT NULL CHECK (mode IN ('ro', 'rw')),
        scope TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'passed', 'failed', 'cancelled')),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        CHECK ((status = 'active' AND ended_at IS NULL) OR (status <> 'active' AND ended_at IS NOT NULL))
    );
CREATE TABLE claims (
        assignment TEXT NOT NULL REFERENCES assignments(id),
        path TEXT NOT NULL,
        PRIMARY KEY (assignment, path)
    );
CREATE TABLE events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL REFERENCES tasks(id),
        actor TEXT NOT NULL,
        type TEXT NOT NULL,
        summary TEXT NOT NULL,
        refs TEXT NOT NULL,
        timestamp TEXT NOT NULL
    );
CREATE INDEX idx_tasks_project_status ON tasks(project, status);
CREATE INDEX idx_assignments_task_status ON assignments(task, status);
CREATE INDEX idx_events_task_seq ON events(task, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT, 'task events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
        BEGIN SELECT RAISE(ABORT, 'task events are append-only'); END;

INSERT INTO schema_version VALUES (1, 1);
INSERT INTO tasks VALUES
('T-0001', '/repo/one', 'Legacy writer', 'Keep history', '["Keep claims"]', 'in_progress', 'claude', '2026-01-01T00:00:00Z', '2026-01-01T00:00:03Z', 4),
('T-0002', '/repo/two', 'Legacy reader', 'Keep another task', '[]', 'in_progress', 'claude', '2026-01-01T00:00:04Z', '2026-01-01T00:00:05Z', 2);
INSERT INTO assignments VALUES
('A-0001', 'T-0001', 'codex', 'builder', 'rw', '["src", "tests"]', 'active', '2026-01-01T00:00:01Z', NULL),
('A-0002', 'T-0001', 'agy', 'reviewer', 'ro', '["."]', 'passed', '2026-01-01T00:00:02Z', '2026-01-01T00:00:03Z'),
('A-0003', 'T-0002', 'agy', 'explorer', 'ro', '[]', 'active', '2026-01-01T00:00:05Z', NULL);
INSERT INTO claims VALUES ('A-0001', 'src'), ('A-0001', 'tests');
INSERT INTO events (seq, task, actor, type, summary, refs, timestamp) VALUES
(1, 'T-0001', 'claude', 'opened', 'Legacy writer', '[]', '2026-01-01T00:00:00Z'),
(2, 'T-0001', 'codex', 'assigned', 'A-0001: builder (rw)', '["A-0001", "src", "tests"]', '2026-01-01T00:00:01Z'),
(3, 'T-0001', 'agy', 'assigned', 'A-0002: reviewer (ro)', '["A-0002", "."]', '2026-01-01T00:00:02Z'),
(4, 'T-0001', 'agy', 'finished', 'passed: Reviewed', '["A-0002"]', '2026-01-01T00:00:03Z'),
(5, 'T-0002', 'claude', 'opened', 'Legacy reader', '[]', '2026-01-01T00:00:04Z'),
(6, 'T-0002', 'agy', 'assigned', 'A-0003: explorer (ro)', '["A-0003"]', '2026-01-01T00:00:05Z');
