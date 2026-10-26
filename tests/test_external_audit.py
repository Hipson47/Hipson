"""Regression contracts from the 2026-10-26 external audit (stdlib runner compatible)."""

import json
import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hipson import agent_install, codex_install
from hipson.ledger import ClaimConflictError, Ledger
from hipson.redaction import redact_text
from tests.test_hipson_helpers import run_cli_inprocess
from tests.test_ledger import _assign, _error, _open, _rows
from tests.test_ledger_mcp import _assert_error, _call, _payload


def test_audit_d01_docs_match_mutation_scope_and_beta_limits():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    introduction = (root / "docs/MUTATION_TRIAGE_NOTES.md").read_text(encoding="utf-8").split("## 1.")[0]
    assert all(f"`{Path(path).name}`" in introduction for path in config["tool"]["mutmut"]["source_paths"])
    safety = (root / "README.md").read_text(encoding="utf-8").split("## Safety in short")[1].split("## Development")[0]
    safety = " ".join(safety.replace("*", "").split())
    assert "beta coordination tool for cooperating agents" in safety
    assert "not a security boundary or an enforced release gate" in safety


def _blocked_task(store, root, kind):
    task = _open(store, root)["id"]
    if kind in ("rw", "ro"):
        _assign(store, task, mode=kind)
    else:
        store.set_status(task, to="in_progress", actor="lead")
        store.add_finding(task, actor="reviewer", severity=kind, title="Unresolved defect")
    store.set_status(task, to="review", actor="lead")
    return task


def test_audit_h01_cli_and_mcp_refuse_done_for_each_blocker(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    for kind in ("rw", "ro", "blocking", "major"):
        with closing(Ledger(db)) as store:
            task = _blocked_task(store, tmp_path / kind, kind)
            before = _rows(store)
        code, _, error = run_cli_inprocess("task", "--db", str(db), "status", "--task", task,
                                          "--to", "done", "--actor", "lead")
        assert code == 1 and "Cannot mark" in error, (kind, code, error)
        _assert_error(_call(tmp_path, "task.status", ledger_db=db, task=task, to="done", actor="lead",
                            approved=True), "Cannot mark")
        with closing(Ledger(db)) as store:
            assert _rows(store) == before


def test_audit_h01_override_requires_reason_and_audits_every_blocker(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _blocked_task(store, tmp_path, "rw")
        reader = _assign(store, task, mode="ro")
        findings = [store.add_finding(task, actor="reviewer", severity=severity, title=severity)
                    for severity in ("blocking", "major", "minor", "info")]
        before = _rows(store)
        _error(lambda: store.set_status(task, to="done", actor="lead", force=True), "reason")
        assert _rows(store) == before
    for options in ({"force": True}, {"force": True, "reason": " "}, {"reason": "reason only"}):
        _assert_error(_call(tmp_path, "task.status", ledger_db=db, task=task, to="done", actor="lead",
                            approved=True, **options), "reason")
    code, _, error = run_cli_inprocess("task", "--db", str(db), "status", "--task", task, "--to", "done",
                                      "--actor", "lead", "--force", "--reason", "Operator exception")
    assert code == 0, error
    with closing(Ledger(db)) as store:
        view, events = store.show(task), store.events(task)
        override, done = events[-2:]
        assert override["type"] == "done_override" and "Operator exception" in override["summary"]
        assert "active assignment" in override["summary"] and "claim" in override["summary"]
        for identifier in (reader["id"], view["claims"][0]["assignment"], *[f["id"] for f in findings[:2]]):
            assert identifier in override["refs"] and identifier in override["summary"]
        assert "src" in override["summary"]
        assert done["type"] == "status" and "review -> done" in done["summary"]
        assert all(f["id"] in done["refs"] and f["id"] in done["summary"] for f in findings[2:])
        assert view["status"] == "done" and len(view["claims"]) == 1
        assert all(a["status"] == "active" for a in view["assignments"])


def test_audit_h01_minor_info_allow_normal_mcp_close(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _blocked_task(store, tmp_path, "minor")
        store.add_finding(task, actor="reviewer", severity="info", title="Follow up")
    view = _payload(_call(tmp_path, "task.status", ledger_db=db, task=task, to="done", actor="lead",
                          summary="Accepted", approved=True))
    assert view["status"] == "done"
    with closing(Ledger(db)) as store:
        event = store.events(task)[-1]
        assert "review -> done" in event["summary"] and "Accepted" in event["summary"]
        assert all(f["id"] in event["refs"] and f["id"] in event["summary"] for f in view["findings"])


def test_audit_h01_mcp_override_and_transactional_failure(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _blocked_task(store, tmp_path, "major")
        before = _rows(store)
        append = store._append_event

        def fail_status(task, actor, type, summary, refs):
            if type == "status":
                raise sqlite3.IntegrityError("simulated status storage failure")
            return append(task, actor, type, summary, refs)

        with patch.object(store, "_append_event", side_effect=fail_status):
            _error(lambda: store.set_status(task, to="done", actor="lead", force=True, reason="Exception"),
                   "simulated", sqlite3.IntegrityError)
        assert _rows(store) == before  # No dangling override if the closure cannot commit.
    view = _payload(_call(tmp_path, "task.status", ledger_db=db, task=task, to="done", actor="lead",
                          force=True, reason="Explicit MCP exception", approved=True))
    assert view["status"] == "done" and view["findings"][0]["status"] == "open"
    with closing(Ledger(db)) as store:
        assert store.events(task)[-2]["type"] == "done_override"
        assert "Explicit MCP exception" in store.events(task)[-2]["summary"]


def test_audit_h01_claims_alone_block_done_and_override_cannot_skip_lifecycle(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _blocked_task(store, tmp_path, "rw")
        store.connection.execute("UPDATE assignments SET status = 'passed', ended_at = started_at")
        before = _rows(store)
        _error(lambda: store.set_status(task, to="done", actor="lead"), "claim")
        assert _rows(store) == before
        store.set_status(task, to="in_progress", actor="lead")
        before = _rows(store)
        _error(lambda: store.set_status(task, to="done", actor="lead", force=True, reason="Exception"),
               "Invalid task transition")
        assert _rows(store) == before
        for options in ({"force": True, "reason": "Exception"}, {"reason": "Exception"}):
            _error(lambda options=options: store.set_status(task, to="review", actor="lead", **options), "reason")


def test_audit_h02_verified_live_owner_outlives_lease(tmp_path: Path):
    for namespace in ("pid:[local]", None):  # macOS/native Windows have no Linux PID namespace.
        db = tmp_path / f"live-{namespace is None}.sqlite"
        with closing(Ledger(db, hostname=lambda: "local", boot_id=lambda: "boot",
                            pid_namespace=lambda namespace=namespace: namespace, pid_alive=lambda pid: True)) as store:
            task = _open(store, tmp_path)["id"]
            owner = _assign(store, task, owner_host="local", owner_pid=42, owner_boot_id="boot",
                            owner_pid_namespace=namespace, lease_seconds=0)
            assert store.reap(dry_run=True) == []
            assert store.reap() == []
            _error(lambda task=task: _assign(store, task), owner["id"], ClaimConflictError)
            assert store.show(task)["assignments"] == [owner]


def test_audit_h02_unverifiable_owner_uses_lease(tmp_path: Path):
    for index, owner in enumerate(({}, {"owner_host": "remote", "owner_pid": 42, "owner_boot_id": "boot"},
                                   {"owner_host": "local", "owner_pid": 42},
                                   {"owner_host": "local", "owner_pid": 42, "owner_boot_id": "boot",
                                    "owner_pid_namespace": "pid:[foreign]"})):
        with closing(Ledger(tmp_path / f"unknown-{index}.sqlite", hostname=lambda: "local",
                            boot_id=lambda: "boot", pid_namespace=lambda: "pid:[local]",
                            pid_alive=lambda pid: True)) as store:
            task = _open(store, tmp_path)["id"]
            previous = _assign(store, task, lease_seconds=0, **owner)
            following = _assign(store, task)
            assert following["id"] != previous["id"]
            assert store.show(task)["assignments"][0]["status"] == "cancelled"
            assert "lease_expired" in store.events(task)[-2]["summary"]


def test_audit_h04_all_ledger_metadata_redacted_at_write(tmp_path: Path):
    secret = "audit-fixture-value-1234567890"
    text = f"api_key={secret}"
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = store.open_task(project=tmp_path, title=text, objective=text, acceptance=[text], lead=text)["id"]
        assignment = store.assign(task, actor=text, role="builder", mode="ro", scope=[text],
                                  owner_host=text, owner_boot_id=text, owner_pid_namespace=text)
        store.add_event(task, actor=text, type=text, summary=text, refs=[text])
        finding = store.add_finding(task, actor=text, severity="major", title=text, detail=text, location=text)
        store.update_finding(finding["id"], status="fixed", actor=text, resolution_note=text)
        report = {"status": "completed", "summary": text, "files_changed": [{"path": text, "change": text}],
                  "commands": [{"command": text, "exit_code": 0}], "findings": [], "risks": [text]}
        store.finish(assignment["id"], result="passed", summary=text, refs=[text], worker_result=report)
        for table in ("tasks", "assignments", "claims", "events", "findings"):
            assert secret not in str([tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}")]), table
        payload = [store.show(task), store.events(task), store.findings(), store.board(), store.list_tasks()]
        assert secret not in json.dumps(payload)
        assert redact_text(text) in json.dumps(payload)


def test_audit_h04_legacy_metadata_readable_but_outputs_redacted(tmp_path: Path):
    secret = "audit-legacy-value-1234567890"
    text = f"api_key={secret}"
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        assignment = _assign(store, task)
        # Emulate pre-fix stored text without rewriting immutable events.
        store.connection.execute("UPDATE tasks SET lead = ?, title = ?", (text, text))
        store.connection.execute("UPDATE assignments SET actor = ?", (text,))
        store.connection.execute("INSERT INTO events (task, actor, type, summary, refs, timestamp) "
                                 "VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
                                 (task, text, text, text, json.dumps([text])))
    with closing(Ledger(db, read_only=True)) as store:
        payload = [store.show(task), store.events(task), store.list_tasks(), store.board()]
        assert secret not in json.dumps(payload)
        assert store.show(task)["assignments"][0]["id"] == assignment["id"]
    code, stdout, stderr = run_cli_inprocess("task", "--db", str(db), "events", "--task", task, "--json")
    assert code == 0 and secret not in stdout + stderr
    for name in ("task.show", "task.events", "task.board", "task.list"):
        arguments = {"task": task} if name in ("task.show", "task.events") else {}
        assert secret not in json.dumps(_payload(_call(tmp_path, name, ledger_db=db, **arguments)))
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT lead FROM tasks").fetchone()[0] == text


def test_audit_h04_secret_paths_rejected_without_renaming_claims(tmp_path: Path):
    text = "api_key=audit-fixture-value-1234567890"
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        _error(lambda: _open(store, tmp_path / text), "Project paths")
        task = _open(store, tmp_path)["id"]
        before = _rows(store)
        _error(lambda: _assign(store, task, scope=[f"src/{text}"]), "Write scope paths")
        assert _rows(store) == before


def test_audit_h06_backups_never_overwrite_with_frozen_clock(tmp_path: Path):
    source = tmp_path / "AGENTS.md"
    with patch.object(codex_install.time, "strftime", return_value="20261026-120000"):
        source.write_text("first", encoding="utf-8")
        first = codex_install.backup_file(source)
        source.write_text("second", encoding="utf-8")
        second = codex_install.backup_file(source)
        assert first != second
        assert first.read_text(encoding="utf-8") == "first"
        assert second.read_text(encoding="utf-8") == "second"
        assert source.read_text(encoding="utf-8") == "second"
        for contents in ("third", "fourth"):
            agent_install._write_preserved_file(source, contents)
        assert sorted(path.read_text(encoding="utf-8") for path in tmp_path.glob("AGENTS.md.backup-*")) == [
            "first", "second", "second", "third",
        ]
