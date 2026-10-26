"""Findings and strict worker-result acceptance tests (also runnable without pytest)."""

import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hipson import ledger
from hipson.ledger import Ledger, load_task_result, validate_task_result
from hipson.redaction import REDACTION
from tests.test_hipson_helpers import run_cli_inprocess
from tests.test_ledger import SCHEMAS, _assign, _error, _open, _rows, assert_strict_schema
from tests.test_ledger_mcp import _assert_error, _call, _payload
from tests.test_ledger_recovery import _v1_file


def _result():
    return {
        "status": "completed", "summary": "Review complete",
        "files_changed": [{"path": "src/hipson/ledger.py", "change": "Add validation"}],
        "commands": [{"command": "python -m pytest -q", "exit_code": 0},
                     {"command": "manual follow-up", "exit_code": None}],
        "findings": [{"severity": "major", "title": "Missing guard", "detail": "Validate before writing",
                      "location": "src/hipson/ledger.py:1"},
                     {"severity": "info", "title": "Consider docs", "detail": "Describe behavior", "location": None}],
        "risks": ["Local verification only"],
    }


def _snapshot(store):
    return {**_rows(store), "findings": [tuple(row) for row in store.connection.execute("SELECT * FROM findings")]}


def _done_task(store, project):
    task = _open(store, project)["id"]
    for status in ("in_progress", "review", "done"):
        store.set_status(task, to=status, actor="lead")
    return task


def _assert_finding_reopened_task(store, task, finding):
    view = store.show(task)
    assert view["status"] == "in_progress"
    assert view["revision"] == len(store.events(task))
    assert any(item["id"] == task for item in store.board())
    reopened = [event for event in store.events(task) if event["type"] == "reopened"]
    assert len(reopened) == 1
    assert finding["id"] in reopened[0]["summary"] and reopened[0]["refs"] == [finding["id"]]


def test_severe_finding_creation_reopens_done_task(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        for severity in ("blocking", "major"):
            task = _done_task(store, tmp_path)
            finding = store.add_finding(task, actor="reviewer", severity=severity, title="Late regression")
            assert finding["status"] == "open"
            _assert_finding_reopened_task(store, task, finding)
            assert not any(event["type"] == "done_override" for event in store.events(task))


def test_finding_reopening_changes_only_done_tasks_with_severe_findings(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        for severity in ledger.FINDING_SEVERITIES:
            for status in ("review", "done", "cancelled"):
                task = _open(store, tmp_path)["id"]
                finding = store.add_finding(task, actor="lead", severity=severity, title="Original issue")
                store.update_finding(finding["id"], status="fixed", actor="lead")
                store.set_status(task, to="in_progress", actor="lead")
                store.set_status(task, to="review", actor="lead")
                if status != "review":
                    store.set_status(task, to=status, actor="lead")
                reopened = store.update_finding(finding["id"], status="open", actor="reviewer",
                                                resolution_note="Reproduced again")
                if status == "done" and severity in ("blocking", "major"):
                    _assert_finding_reopened_task(store, task, reopened)
                else:
                    assert store.show(task)["status"] == status
                    assert not any(event["type"] == "reopened" for event in store.events(task))
        for severity in ("minor", "info"):
            task = _done_task(store, tmp_path)
            store.add_finding(task, actor="reviewer", severity=severity, title="Optional follow-up")
            assert store.show(task)["status"] == "done"


def test_reviewer_result_reopens_forced_done_task(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        assignment = store.assign(task, actor="reviewer", role="reviewer", mode="ro")
        store.set_status(task, to="review", actor="lead")
        store.set_status(task, to="done", actor="lead", force=True, reason="Review still running")
        store.finish(assignment["id"], result="passed", summary="Review complete", worker_result=_result())
        finding = store.findings(task=task, severity="major")[0]
        assert finding["source_assignment"] == assignment["id"]
        _assert_finding_reopened_task(store, task, finding)
        assert store.show(task)["assignments"][0]["status"] == "passed"


def test_finding_and_task_reopening_rollback_together(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        resolved = store.add_finding(task, actor="lead", severity="major", title="Resolved issue")
        store.update_finding(resolved["id"], status="fixed", actor="lead")
        for status in ("in_progress", "review", "done"):
            store.set_status(task, to=status, actor="lead")
        before = _snapshot(store)
        store.connection.execute("""CREATE TRIGGER reject_reopen BEFORE INSERT ON events WHEN NEW.type = 'reopened'
                                  BEGIN SELECT RAISE(ABORT, 'injected reopen failure'); END""")
        operations = [
            lambda: store.add_finding(task, actor="reviewer", severity="major", title="New issue"),
            lambda: store.update_finding(resolved["id"], status="open", actor="reviewer", resolution_note="Still fails"),
        ]
        for operation in operations:
            _error(operation, "injected reopen failure", sqlite3.IntegrityError)
            assert _snapshot(store) == before


def test_finding_waiting_for_closure_reopens_after_commit(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        store.set_status(task, to="in_progress", actor="lead")
        store.set_status(task, to="review", actor="lead")
    closing_task = threading.Event()
    finding_ready = threading.Event()
    adding_finding = threading.Event()
    release_closure = threading.Event()

    def close_task():
        with closing(Ledger(db)) as store:
            append = store._append_event

            def pause_commit(*args):
                event = append(*args)
                closing_task.set()
                assert release_closure.wait(10), "Finding writer never started"
                return event

            with patch.object(store, "_append_event", pause_commit):
                assert finding_ready.wait(10), "Finding connection never opened"
                store.set_status(task, to="done", actor="lead")

    def add_finding():
        with closing(Ledger(db)) as store:
            finding_ready.set()
            assert closing_task.wait(10), "Closure never acquired writer lock"
            adding_finding.set()
            return store.add_finding(task, actor="reviewer", severity="major", title="Concurrent regression")

    with ThreadPoolExecutor(max_workers=2) as pool:
        closed = pool.submit(close_task)
        added = pool.submit(add_finding)
        try:
            assert closing_task.wait(10), "Closure never acquired writer lock"
            assert adding_finding.wait(10), "Finding writer never started"
        finally:
            release_closure.set()
        closed.result(timeout=15)
        finding = added.result(timeout=15)
    with closing(Ledger(db, read_only=True)) as store:
        _assert_finding_reopened_task(store, task, finding)


def test_cli_and_mcp_severe_findings_reopen_done_tasks(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    for interface in ("cli", "mcp"):
        for action in ("add", "reopen"):
            with closing(Ledger(db)) as store:
                task = _open(store, tmp_path)["id"]
                if action == "reopen":
                    finding = store.add_finding(task, actor="lead", severity="major", title="Old issue")
                    store.update_finding(finding["id"], status="fixed", actor="lead")
                for status in ("in_progress", "review", "done"):
                    store.set_status(task, to=status, actor="lead")
            if interface == "cli":
                arguments = (["add", "--task", task, "--severity", "major", "--title", "Late issue"]
                             if action == "add" else
                             ["update", "--finding", finding["id"], "--status", "open", "--note", "Reproduced"])
                rc, stdout, stderr = run_cli_inprocess(
                    "task", "finding", *arguments, "--db", str(db), "--actor", "reviewer", "--json")
                assert rc == 0, stderr
                finding = json.loads(stdout)
            else:
                arguments = ({"task": task, "severity": "major", "title": "Late issue"} if action == "add" else
                             {"finding": finding["id"], "status": "open", "resolution_note": "Reproduced"})
                finding = _payload(_call(tmp_path, "task.finding_add" if action == "add" else "task.finding_update",
                                         ledger_db=db, actor="reviewer", approved=True, **arguments))
            with closing(Ledger(db, read_only=True)) as store:
                assert store.show(task)["status"] == "in_progress"
                assert finding["status"] == "open"


def _v2_file(db):
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        for statement in ledger.MIGRATION_V1_TO_V2:
            connection.execute(statement)
        # Preserve populated ownership as well as legacy NULL values.
        connection.execute("UPDATE assignments SET owner_pid = 42, owner_host = 'remote' WHERE id = 'A-0001'")
        connection.commit()


def test_finding_lifecycle_filters_reopen_and_atomic_events(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        source = _assign(store, task)["id"]
        for index, status in enumerate(("fixed", "wontfix", "duplicate"), start=1):
            before = store.show(task)["revision"]
            finding = store.add_finding(task, actor="lead", severity="major", title="Missing guard",
                                        detail="Validate first", location="ledger.py:1", source_assignment=source)
            assert finding["id"] == f"F-{index:04d}" and finding["status"] == "open"
            assert finding["source_assignment"] == source and finding["resolved_at"] is None
            assert store.show(task)["revision"] == before + 1
            event = store.events(task)[-1]
            assert event["type"] == "finding_opened" and event["refs"] == [finding["id"]]
            resolved = store.update_finding(finding["id"], actor="lead", status=status,
                                            resolution_note="Reviewed", resolved_by_assignment=source)
            assert resolved["status"] == status and resolved["resolved_by_assignment"] == source
            assert resolved["created_at"] == finding["created_at"] <= resolved["updated_at"]
            assert resolved["resolved_at"] == resolved["updated_at"]
            assert store.show(task)["findings"] == []
            assert store.findings(task=task, status=status, severity="major")[-1] == resolved
            assert store.events(task)[-1]["type"] == "finding_resolved"
            assert store.events(task)[-1]["refs"] == [finding["id"], source]
            reopened = store.update_finding(finding["id"], actor="lead", status="open", resolution_note="Still fails")
            assert reopened["resolved_at"] is None and reopened["resolved_by_assignment"] is None
            assert reopened["resolution_note"] == "Still fails"
            assert store.events(task)[-1]["type"] == "finding_reopened"
            assert store.show(task)["revision"] == before + 3
            store.update_finding(finding["id"], actor="lead", status=status)
        other = _open(store, tmp_path)["id"]
        fresh = store.add_finding(other, actor="lead", severity="blocking", title="Stop")
        assert fresh["id"] == "F-0004" and fresh["source_assignment"] is None
        assert store.findings(status="open") == [fresh]
        assert store.findings(task=task, severity="minor") == []
        assert len(store.findings(task=task)) == 3
        assert store.show(other)["findings"] == [fresh]
        assert_strict_schema("task.schema.json", store.show(other))
        assert store.show(task)["revision"] == len(store.events(task))


def test_finding_cross_task_resolution_cli_mcp_and_events(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        review_task = _open(store, tmp_path)["id"]
        fix_task = _open(store, tmp_path)["id"]
        fix = _assign(store, fix_task)["id"]
        finding = store.add_finding(review_task, actor="lead", severity="major", title="Fix elsewhere")
        fix_revision = store.show(fix_task)["revision"]
    rc, stdout, stderr = run_cli_inprocess(
        "task", "finding", "update", "--db", str(db), "--finding", finding["id"], "--status", "fixed",
        "--actor", "lead", "--resolved-by", fix, "--note", "Verified follow-up", "--json")
    assert rc == 0, stderr
    assert json.loads(stdout)["resolved_by_assignment"] == fix
    events = _payload(_call(tmp_path, "task.events", ledger_db=db, task=review_task))
    assert events[-1]["refs"] == [finding["id"], fix, fix_task]
    assert fix_task in events[-1]["summary"]
    reopened = _payload(_call(tmp_path, "task.finding_update", ledger_db=db, finding=finding["id"],
                              status="open", actor="lead", resolution_note="Recheck", approved=True))
    assert reopened["resolved_by_assignment"] is None
    resolved = _payload(_call(tmp_path, "task.finding_update", ledger_db=db, finding=finding["id"],
                              status="fixed", actor="lead", resolved_by_assignment=fix, approved=True))
    assert resolved["task"] == review_task and resolved["resolved_by_assignment"] == fix
    with closing(Ledger(db, read_only=True)) as store:
        assert store.findings(task=review_task, status="fixed") == [resolved]
        assert store.show(fix_task)["revision"] == fix_revision
        assert store.events(review_task)[-1]["refs"] == [finding["id"], fix, fix_task]


def test_finding_invalid_transitions_and_source_links_write_nothing(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        other = _open(store, tmp_path)["id"]
        other_assignment = _assign(store, other)["id"]
        finding = store.add_finding(task, actor="lead", severity="minor", title="Fix")
        before = _snapshot(store)
        for fn, message in (
            (lambda: store.update_finding(finding["id"], actor="lead", status="open"), "Invalid finding transition"),
            (lambda: store.update_finding(finding["id"], actor="lead", status="bad"), "Finding status"),
            (lambda: store.update_finding("F-9999", actor="lead", status="fixed"), "Finding not found"),
            (lambda: store.update_finding(finding["id"], actor="lead", status="fixed",
                                          resolved_by_assignment="A-9999"), "Assignment not found"),
            (lambda: store.add_finding(task, actor="lead", severity="major", title="Wrong",
                                       source_assignment=other_assignment), "does not belong"),
            (lambda: store.add_finding(task, actor="lead", severity="major", title="Wrong",
                                       source_assignment="A-9999"), "Assignment not found"),
            (lambda: store.add_finding(task, actor="lead", severity="bad", title="Fix"), "Finding severity"),
            (lambda: store.add_finding(task, actor="lead", severity="info", title="  "), "title must not be empty"),
            (lambda: store.add_finding("T-9999", actor="lead", severity="info", title="Fix"), "Task not found"),
            (lambda: store.findings(status="bad"), "Finding status"),
            (lambda: store.findings(severity="bad"), "Finding severity"),
            (lambda: store.findings(task="T-9999"), "Task not found"),
        ):
            _error(fn, message)
            assert _snapshot(store) == before
        store.update_finding(finding["id"], actor="lead", status="fixed")
        before = _snapshot(store)
        for status, note, assignment, message in (
            ("fixed", "", None, "Invalid finding transition"),
            ("wontfix", "", None, "Invalid finding transition"),
            ("duplicate", "", None, "Invalid finding transition"),
            ("open", " \n", None, "Reopening note must not be empty"),
            ("open", "Still fails", other_assignment, "reopened finding cannot"),
        ):
            _error(lambda status=status, note=note, assignment=assignment: store.update_finding(finding["id"], actor="lead", status=status,
                                                resolution_note=note, resolved_by_assignment=assignment), message)
            assert _snapshot(store) == before


def test_finding_event_failure_rolls_back_creation_and_resolution(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        finding = store.add_finding(task, actor="lead", severity="major", title="Fix")
        before = _snapshot(store)
        with patch.object(store, "_append_event", side_effect=sqlite3.OperationalError("injected event failure")):
            _error(lambda: store.add_finding(task, actor="lead", severity="minor", title="New"),
                   "injected", sqlite3.OperationalError)
            _error(lambda: store.update_finding(finding["id"], actor="lead", status="fixed"),
                   "injected", sqlite3.OperationalError)
        assert _snapshot(store) == before


def test_cross_task_resolution_event_failure_rolls_back(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        fix_task = _open(store, tmp_path)["id"]
        fix = _assign(store, fix_task)["id"]
        finding = store.add_finding(task, actor="lead", severity="major", title="Follow-up")
        before = _snapshot(store)
        with patch.object(store, "_append_event", side_effect=sqlite3.OperationalError("event failure")):
            _error(lambda: store.update_finding(finding["id"], actor="lead", status="fixed", resolved_by_assignment=fix),
                   "event failure", sqlite3.OperationalError)
        assert _snapshot(store) == before


def test_worker_result_schema_is_strict_closed_required_and_matches_runtime():
    schema = json.loads((SCHEMAS / "task-result.schema.json").read_text())
    assert schema == ledger.TASK_RESULT_SCHEMA

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(schema)
    assert_strict_schema("task-result.schema.json", _result())
    assert validate_task_result(_result()) == _result()
    task_schema = json.loads((SCHEMAS / "task.schema.json").read_text())
    embedded = {**task_schema["$defs"]["result"], "type": "object"}
    assert embedded == {key: value for key, value in schema.items() if key not in ("$schema", "title")}


def test_result_validation_rejects_missing_extra_wrong_types_and_enums():
    invalid = [None, [], "report", 1, True]
    for key in _result():
        value = _result()
        del value[key]
        invalid.append(value)
    for field, item in (("status", "passed"), ("summary", None), ("files_changed", {}),
                        ("commands", "pytest"), ("findings", ["text"]), ("risks", [1]), ("extra", "unknown")):
        invalid.append({**_result(), field: item})
    for field, index, key, value in (
        ("files_changed", 0, "path", None), ("files_changed", 0, "change", 1),
        ("files_changed", 0, "extra", "unknown"), ("commands", 0, "exit_code", True),
        ("commands", 0, "exit_code", 0.0), ("commands", 0, "exit_code", "0"),
        ("commands", 0, "extra", None), ("commands", 0, "command", None),
        ("findings", 0, "severity", "critical"), ("findings", 0, "title", " \n"),
        ("findings", 0, "detail", None), ("findings", 0, "location", 1),
        ("findings", 0, "extra", "unknown"),
    ):
        result = _result()
        result[field][index][key] = value
        invalid.append(result)
    for field in ("files_changed", "commands", "findings"):
        for key in _result()[field][0]:
            result = _result()
            del result[field][0][key]
            invalid.append(result)
    for value in invalid:
        _error(lambda value=value: validate_task_result(value), "Invalid worker result")


def test_reviewer_result_creates_findings_only_for_reviewers_and_counts(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        for role in ("builder", "explorer", "researcher", "reviewer"):
            assignment = store.assign(task, actor="codex", role=role, mode="rw", scope=["src"])
            report = _result()
            finished = store.finish(assignment["id"], result="passed", summary="Reviewed", worker_result=report)
            assert finished["result"] == report and store.show(task)["claims"] == []
            assert_strict_schema("task.schema.json", store.show(task))
            event = store.events(task)[-1]
            assert event["type"] == "finished" and event["refs"] == [assignment["id"]]
            assert "files_changed=1; commands=2; findings=2; risks=1" in event["summary"]
            findings = store.findings(task=task)
            if role == "reviewer":
                assert len(findings) == 2
                assert [item["source_assignment"] for item in findings] == [assignment["id"]] * 2
                assert all(item["status"] == "open" for item in findings)
                assert findings[1]["location"] == ""
                assert [event["type"] for event in store.events(task)[-3:]] == [
                    "finding_opened", "finding_opened", "finished",
                ]
            else:
                assert findings == []
        before = _snapshot(store)
        _error(lambda: store.finish(assignment["id"], result="passed", summary="Retry", worker_result=_result()),
               "already finished")
        assert _snapshot(store) == before
    with closing(Ledger(tmp_path / "ledger.sqlite", read_only=True)) as store:
        assert len(store.show(task)["findings"]) == 2
        assert store.show(task)["assignments"][-1]["result"] == _result()


def test_result_and_generated_findings_rollback_together_on_finish_event_failure(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        assignment = store.assign(task, actor="reviewer", role="reviewer", mode="rw", scope=["src"])
        before = _snapshot(store)
        store.connection.execute("""CREATE TRIGGER reject_finish BEFORE INSERT ON events WHEN NEW.type = 'finished'
                                  BEGIN SELECT RAISE(ABORT, 'injected finish failure'); END""")
        _error(lambda: store.finish(assignment["id"], result="passed", summary="Done", worker_result=_result()),
               "injected finish failure", sqlite3.IntegrityError)
        assert _snapshot(store) == before
        _error(lambda: store.finish(assignment["id"], result="passed", summary="Done", worker_result={}),
               "Invalid worker result")
        assert _snapshot(store) == before


def test_result_and_finding_text_redacted_before_persistence(tmp_path: Path):
    secret = "sk-" + "r" * 30
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        finding = store.add_finding(task, actor="lead", severity="minor", title=secret, detail=secret, location=secret)
        resolved = store.update_finding(finding["id"], actor="lead", status="fixed", resolution_note=secret)
        assert all(resolved[key] == REDACTION for key in ("title", "detail", "location", "resolution_note"))
        assignment = store.assign(task, actor="codex", role="reviewer", mode="ro")
        result = _result()
        result["summary"] = secret
        result["files_changed"] = [{"path": secret, "change": secret}]
        result["commands"] = [{"command": secret, "exit_code": None}]
        result["findings"] = [{"severity": "major", "title": secret, "detail": secret, "location": secret}]
        result["risks"] = [secret]
        original = copy.deepcopy(result)
        finished = store.finish(assignment["id"], result="failed", summary=secret, worker_result=result)
        assert result == original  # Validation never modifies caller-owned input.
        assert secret not in json.dumps(finished) and REDACTION in json.dumps(finished)
        assert secret not in json.dumps(_snapshot(store))
        assert secret not in "\n".join(store.connection.iterdump())


def test_v2_migration_preserves_rows_events_and_is_transactional(tmp_path: Path):
    db = tmp_path / "v2.sqlite"
    _v2_file(db)
    with closing(sqlite3.connect(db)) as connection:
        before = _rows(SimpleNamespace(connection=connection))
    statements = (*ledger.MIGRATION_V2_TO_V3[:-1], "SELECT * FROM injected_missing_table")
    with patch.object(ledger, "MIGRATION_V2_TO_V3", statements):
        _error(lambda: Ledger(db), "injected_missing_table", sqlite3.OperationalError)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        assert len(connection.execute("PRAGMA table_info(assignments)").fetchall()) == 13
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'findings'").fetchall() == []
        assert _rows(SimpleNamespace(connection=connection)) == before
    with closing(Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 5
        after = _rows(store)
        for table in ("tasks", "events", "claims"):
            assert after[table] == before[table]
        assert [row[:-6] for row in after["assignments"]] == before["assignments"]
        assert all(row[-6:] == (None, None, None, None, None, "") for row in after["assignments"])
        assert store.findings() == []
        finding = store.add_finding("T-0001", actor="lead", severity="major", title="Legacy follow-up")
        assert finding["id"] == "F-0001" and store.events("T-0001")[-1]["seq"] == 7
    with closing(Ledger(db)) as store:
        assert store.findings() == [finding]  # A second open never repeats DDL.


def test_v1_to_v3_migration_failure_rolls_back_both_steps(tmp_path: Path):
    db = tmp_path / "v1.sqlite"
    _v1_file(db)
    with patch.object(ledger, "MIGRATION_V2_TO_V3", (*ledger.MIGRATION_V2_TO_V3, "SELECT * FROM missing")):
        _error(lambda: Ledger(db), "missing", sqlite3.OperationalError)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert len(connection.execute("PRAGMA table_info(assignments)").fetchall()) == 9


def test_v2_reads_return_empty_findings_null_results_without_migration(tmp_path: Path):
    db = tmp_path / "v2.sqlite"
    _v2_file(db)
    original = db.read_bytes()
    with closing(Ledger(db, read_only=True)) as store:
        view = store.show("T-0001")
        assert view["findings"] == [] and all(item["result"] is None for item in view["assignments"])
        assert store.findings(task="T-0001") == [] and store.board()
        assert_strict_schema("task.schema.json", view)
    for command in (("show", "--task", "T-0001"), ("finding", "list"),
                    ("finding", "list", "--task", "T-0001")):
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), *command, "--json")
        assert rc == 0, stderr
        assert json.loads(stdout) == (view if command[0] == "show" else [])
    assert _payload(_call(tmp_path, "task.findings", ledger_db=db, task="T-0001")) == []
    assert _payload(_call(tmp_path, "task.show", ledger_db=db, task="T-0001")) == view
    assert db.read_bytes() == original


def test_cli_result_file_and_compact_show(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(_result()))
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        assignment = store.assign(task, actor="codex", role="reviewer", mode="ro")["id"]
    rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), "finish", "--assignment", assignment,
                                          "--result", "passed", "--summary", "Reviewed",
                                          "--result-file", str(result_path), "--json")
    assert rc == 0, stderr
    assert json.loads(stdout)["result"] == _result()
    rc, stdout, stderr = run_cli_inprocess("task", "show", "--task", task, "--db", str(db))
    assert rc == 0, stderr
    assert "Result [completed]: Review complete (1 files, 2 commands, 2 findings, 1 risks)" in stdout
    assert "Open findings: 2" in stdout and "F-0001 [major] Missing guard" in stdout
    assert "src/hipson/ledger.py:1" in stdout
    assert "manual follow-up" not in stdout  # Commands belong to the full JSON view.


def test_legacy_reader_sees_findings_after_another_connection_migrates(tmp_path: Path):
    db = tmp_path / "v2.sqlite"
    _v2_file(db)
    with closing(Ledger(db, read_only=True)) as reader:
        assert reader.findings(task="T-0001") == []
        with closing(Ledger(db)) as writer:
            finding = writer.add_finding("T-0001", actor="lead", severity="major", title="Follow-up")
        assert reader.show("T-0001")["findings"] == [finding]


def test_cli_finding_add_update_list_and_missing_reads(tmp_path: Path):
    db = tmp_path / "missing" / "ledger.sqlite"
    rc, stdout, stderr = run_cli_inprocess("task", "finding", "list", "--db", str(db), "--json")
    assert rc == 0 and json.loads(stdout) == [] and not db.parent.exists(), stderr
    rc, _, stderr = run_cli_inprocess("task", "finding", "list", "--task", "T-0001", "--db", str(db))
    assert rc == 1 and "Task not found" in stderr and not db.exists()
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]

    def cli(*args):
        rc, stdout, stderr = run_cli_inprocess("task", "--db", str(db), "--json", "finding", *args)
        assert rc == 0, stderr
        return json.loads(stdout)

    finding = cli("add", "--task", task, "--actor", "lead", "--severity", "minor", "--title", "Docs",
                  "--detail", "Expand explanation", "--location", "README:1")
    assert cli("list", "--task", task, "--status", "open", "--severity", "minor") == [finding]
    resolved = cli("update", "--finding", finding["id"], "--status", "fixed", "--actor", "lead", "--note", "Done")
    assert cli("list", "--status", "fixed") == [resolved]
    reopened = cli("update", "--finding", finding["id"], "--to", "open", "--actor", "lead", "--note", "Still needed")
    assert reopened["status"] == "open"


def test_invalid_result_file_never_opens_creates_or_migrates_ledger(tmp_path: Path):
    existing = tmp_path / "v2.sqlite"
    _v2_file(existing)
    original = existing.read_bytes()
    result_path = tmp_path / "bad.json"
    for contents in ('{}', '[]', '{"status":', '{"status":"completed","status":"blocked"}',
                     '{"status":NaN}', json.dumps({**_result(), "risks": [1]})):
        result_path.write_text(contents)
        for db in (existing, tmp_path / "missing" / "ledger.sqlite"):
            with patch("hipson.cli.ledger.Ledger", side_effect=AssertionError("must not open store")):
                rc, _, stderr = run_cli_inprocess("task", "finish", "--db", str(db), "--assignment", "A-0001",
                                                  "--result", "passed", "--summary", "Done",
                                                  "--result-file", str(result_path))
            assert rc == 1 and "Invalid worker result" in stderr
            assert existing.read_bytes() == original and not (tmp_path / "missing").exists()
    result_path.write_bytes(b'\xff')
    _error(lambda: load_task_result(result_path), "expected UTF-8")


def test_mcp_finding_lifecycle_and_structured_finish(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        assignment = store.assign(task, actor="codex", role="reviewer", mode="ro")["id"]
    result = _payload(_call(tmp_path, "task.finish", ledger_db=db, assignment=assignment, result=_result(),
                            summary="Reviewed", approved=True))
    assert result["result"] == _result() and result["status"] == "passed"
    findings = _payload(_call(tmp_path, "task.findings", ledger_db=db, task=task, status="open", severity="major"))
    assert len(findings) == 1 and findings[0]["source_assignment"] == assignment
    added = _payload(_call(tmp_path, "task.finding_add", ledger_db=db, task=task, actor="lead", severity="minor",
                           title="Docs", source_assignment=assignment, detail="Expand", location="README:1", approved=True))
    resolved = _payload(_call(tmp_path, "task.finding_update", ledger_db=db, finding=added["id"], status="fixed",
                              actor="lead", resolved_by_assignment=assignment, resolution_note="Done", approved=True))
    assert resolved["resolved_by_assignment"] == assignment
    reopened = _payload(_call(tmp_path, "task.finding_update", ledger_db=db, finding=added["id"], status="open",
                              actor="lead", resolution_note="Still fails", approved=True))
    assert reopened["status"] == "open" and reopened["resolved_by_assignment"] is None
    for status in ("partial", "blocked"):
        with closing(Ledger(db)) as store:
            assignment = _assign(store, task)["id"]
        report = {**_result(), "status": status}
        finished = _payload(_call(tmp_path, "task.finish", ledger_db=db, assignment=assignment,
                                  result=report, summary="Reported", approved=True))
        assert finished["status"] == "failed" and finished["result"] == report
    view = _payload(_call(tmp_path, "task.show", ledger_db=db, task=task))
    assert len(view["findings"]) == 3
    assert_strict_schema("task.schema.json", view)


def test_mcp_finding_and_result_validation_precedes_store_access(tmp_path: Path):
    db = tmp_path / "missing" / "ledger.sqlite"
    add = {"task": "T-0001", "actor": "lead", "severity": "major", "title": "Fix"}
    update = {"finding": "F-0001", "actor": "lead", "status": "fixed"}
    bad_calls = [
        ("task.findings", {"db": "other.sqlite"}), ("task.findings", {"severity": "critical"}),
        ("task.findings", {"status": "closed"}), ("task.findings", {"task": None}),
        ("task.finding_add", {**add, "severity": "critical"}), ("task.finding_add", {**add, "title": "  "}),
        ("task.finding_add", {**add, "actor": " "}), ("task.finding_add", {**add, "detail": 1}),
        ("task.finding_add", {**add, "source_assignment": None}), ("task.finding_add", {}),
        ("task.finding_update", {**update, "status": "closed"}),
        ("task.finding_update", {**update, "resolution_note": False}),
        ("task.finding_update", {**update, "resolved_by_assignment": 1}),
        ("task.finding_update", {**update, "status": "open"}),
        ("task.finding_update", {**update, "status": "open", "resolution_note": "Again", "resolved_by_assignment": "A-1"}),
        ("task.finding_update", {**update, "unknown": "x"}),
        ("task.finish", {"assignment": "A-0001", "summary": "Done", "result": {}}),
        ("task.finish", {"assignment": "A-0001", "summary": "Done", "result": {**_result(), "extra": 1}}),
    ]
    for name, arguments in bad_calls:
        args = {**arguments, **({"approved": True} if name != "task.findings" else {})}
        with patch("hipson.mcp_server.ledger.Ledger", side_effect=AssertionError("must not open store")):
            result = _call(tmp_path, name, ledger_db=db, **args)
        _assert_error(result, "")
        assert not db.parent.exists()
    assert _payload(_call(tmp_path, "task.findings", ledger_db=db)) == []
    _assert_error(_call(tmp_path, "task.findings", ledger_db=db, task="T-0001"), "Task not found")
    assert not db.parent.exists()
