import contextlib
import io
import json
import sqlite3
from pathlib import Path

from hipson import cli
from hipson import session as hipson_session
from hipson.redaction import REDACTION
from hipson.session import open_session_store


def run_cli(*args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = cli.main(list(args))
    except SystemExit as exc:
        rc = int(exc.code or 0) if isinstance(exc.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


def test_session_store_bounds_and_redacts_direct_tool_call_payloads(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    store = open_session_store(tmp_path / "runtime.sqlite")
    try:
        session_id = store.create_session(cwd=str(tmp_path), title=f"session {secret}")
        message_id = store.add_message(
            session_id,
            "assistant",
            f"provider body OPENROUTER_API_KEY={secret}",
            {"detail": f"Bearer abc123secret4567890 {secret}"},
        )
        store.add_tool_call(
            session_id,
            message_id=message_id,
            tool_name="demo.large",
            input_data={"prompt": f"token={secret}", "blob": "i" * 8_000},
            output_data={
                "markdown": f"OPENROUTER_API_KEY={secret}\n" + ("o" * 8_000),
                "items": [{"value": index} for index in range(100)],
            },
            error=f"provider failed with password=hunter2 {secret}",
        )
        approval_id = store.add_approval_record(
            session_id=session_id,
            source="test",
            tool_name="demo.large",
            risk_level="read",
            decision="approved",
            reason=f"allowed with token={secret}",
            approved_by="policy",
            expires_at="2026-05-27T23:59:59Z",
        )

        sessions = store.list_sessions()
        messages = store.list_messages(session_id)
        tool_calls = store.list_tool_calls(session_id)
        approvals = store.list_approval_records(session_id=session_id)
    finally:
        store.close()

    rendered = f"{sessions} {messages} {tool_calls} {approvals}"
    assert secret not in rendered
    assert "abc123secret4567890" not in rendered
    assert "hunter2" not in rendered
    assert REDACTION in rendered
    assert "truncated" in str(tool_calls[0]["input"])
    assert "truncated" in str(tool_calls[0]["output"])
    assert len(str(tool_calls[0]["input"])) < 1_500
    assert len(str(tool_calls[0]["output"])) < 2_500
    assert approvals[0]["id"] == approval_id
    assert approvals[0]["decision"] == "approved"
    assert approvals[0]["reason"] == "allowed with token=[REDACTED]"
    assert approvals[0]["expires_at"] == "2026-05-27T23:59:59Z"


def test_approval_record_migration_adds_expires_at_to_existing_db(tmp_path: Path):
    db_path = tmp_path / "runtime.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(hipson_session.INITIAL_SCHEMA)
        connection.execute(
            """
            CREATE TABLE schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (hipson_session.SCHEMA_VERSION, hipson_session.timestamp()),
        )
        connection.execute(
            """
            CREATE TABLE approval_records (
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
            )
            """
        )

    store = open_session_store(db_path)
    try:
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(approval_records)")}
        session_id = store.create_session(cwd=str(tmp_path), title="Approval migration")
        store.add_approval_record(
            session_id=session_id,
            source="runtime",
            tool_name="repo.scan",
            risk_level="read",
            decision="approved",
            reason="migration check",
            expires_at="2026-05-27T23:59:59Z",
        )
        approvals = store.list_approval_records(session_id=session_id)
    finally:
        store.close()

    assert "expires_at" in columns
    assert approvals[0]["expires_at"] == "2026-05-27T23:59:59Z"


def test_session_cli_list_show_and_search_redact_temp_db(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    db_path = tmp_path / "runtime.sqlite"
    store = open_session_store(db_path)
    try:
        session_id = store.create_session(cwd=str(tmp_path), repo_root=str(tmp_path), title="Runtime Debug")
        message_id = store.add_message(session_id, "user", f"Searchable packet-first note {secret}")
        store.add_message(session_id, "assistant", "Use approval-gated learning.")
        store.add_tool_call(
            session_id,
            message_id=message_id,
            tool_name="repo.scan",
            input_data={"path": ".", "secret": secret},
            output_data={"markdown": "bounded " + secret},
            risk_level="read",
            status="completed",
        )
        store.add_approval_record(
            session_id=session_id,
            source="runtime",
            tool_name="repo.scan",
            risk_level="read",
            decision="approved",
            reason="Policy allowed tool execution",
        )
        store.add_memory(
            session_id=session_id,
            scope="repo",
            repo="Hipson",
            kind="decision",
            summary=f"Remember searchable tool trajectory {secret}",
            source_refs=[f"session:{session_id}"],
        )
    finally:
        store.close()

    rc, stdout, stderr = run_cli("session", "list", "--session-db", str(db_path))
    assert rc == 0
    assert stderr == ""
    assert session_id in stdout
    assert "messages=2" in stdout
    assert "tools=1" in stdout

    rc, stdout, stderr = run_cli("session", "show", session_id, "--session-db", str(db_path))
    assert rc == 0
    assert stderr == ""
    assert "repo.scan" in stdout
    assert "approval_records:" in stdout
    assert "Searchable packet-first note" in stdout
    assert secret not in stdout
    assert REDACTION in stdout

    rc, stdout, stderr = run_cli("session", "search", "packet-first", "--session-db", str(db_path), "--json")
    assert rc == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["search_backend"] in {"fts+fallback", "fallback"}
    assert payload["results"][0]["session_id"] == session_id
    assert "packet-first" in payload["results"][0]["snippet"]
    assert secret not in stdout

    rc, stdout, stderr = run_cli("session", "search", "repo.scan", "--session-db", str(db_path), "--json")
    assert rc == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert any(result["kind"] == "tool_call" for result in payload["results"])

    rc, stdout, stderr = run_cli("session", "search", "trajectory", "--session-db", str(db_path), "--json")
    assert rc == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert any(result["kind"] == "memory" for result in payload["results"])
    assert secret not in stdout


def test_session_cli_missing_db_is_read_only_and_missing_session_fails(tmp_path: Path):
    missing_db = tmp_path / "missing.sqlite"

    rc, stdout, stderr = run_cli("session", "list", "--session-db", str(missing_db))
    assert rc == 0
    assert stdout.strip() == "No sessions found."
    assert stderr == ""
    assert not missing_db.exists()

    rc, stdout, stderr = run_cli("session", "show", "missing", "--session-db", str(missing_db))
    assert rc == 1
    assert stdout == ""
    assert "Session DB does not exist" in stderr
