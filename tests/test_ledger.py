"""Ledger acceptance tests, also runnable by scripts/run_tests.py without pytest."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from hipson import contracts
from hipson.ledger import BUSY_TIMEOUT_MS, ClaimConflictError, Ledger, LedgerError, default_ledger_db, normalize_scope
from hipson.redaction import REDACTION
from tests.json_schema import schema_errors
from tests.test_hipson_helpers import assert_schema_valid, run_cli, run_cli_inprocess


def _open(store, project, title="Ledger"):
    return store.open_task(
        project=project, title=title, objective="Track ownership", acceptance=["Claims do not overlap"]
    )


def _assign(store, task, scope=("src",), mode="rw", actor="codex", **owner):
    return store.assign(task, actor=actor, role="builder", mode=mode, scope=scope, **owner)


def _error(fn, message, error=LedgerError):
    try:
        fn()
    except error as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected {error.__name__}: {message}")


def _rows(store):
    return {
        table: [tuple(row) for row in store.connection.execute(f"SELECT * FROM {table}")]
        for table in ("tasks", "assignments", "claims", "events")
    }


SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def _strict_errors(schema, payload, root, path="$"):
    """Compatibility alias for ledger controls using the shared strict validator."""
    return schema_errors(schema, payload, root, path)


def assert_strict_schema(name, payload):
    root = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    errors = _strict_errors(root, payload, root)
    assert not errors, errors


def test_ledger_strict_schema_validator_rejects_malformed_payloads(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        _assign(store, task)
        view = store.show(task)
        event = store.events(task)[-1]
    assert_strict_schema("task.schema.json", view)
    assert_strict_schema("task-event.schema.json", event)
    root = json.loads((SCHEMAS / "task.schema.json").read_text(encoding="utf-8"))
    bad_assignment = json.loads(json.dumps(view))
    bad_assignment["assignments"][0]["id"] = "A-1"
    bad_claim = json.loads(json.dumps(view))
    bad_claim["claims"][0]["extra"] = True
    bad_revision = {**view, "revision": 0}
    for payload, expected in (
        (bad_assignment, "$.assignments[0].id: pattern"),
        (bad_claim, "$.claims[0].extra: additional"),
        (bad_revision, "$.revision: minimum"),
    ):
        assert expected in _strict_errors(root, payload, root)


def _readline(process):
    """Read a subprocess barrier with a deadline, including on runner failures."""
    with ThreadPoolExecutor(max_workers=1) as reader:
        pending = reader.submit(process.stdout.readline)
        try:
            return pending.result(timeout=30).strip()
        except TimeoutError:
            process.kill()
            raise


def test_ledger_full_lifecycle_and_revision(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path / "repo" / ".." / "repo")
        assert task["id"] == "T-0001"
        assert task["project"] == str((tmp_path / "repo").resolve())
        assert task["status"] == "open" and task["lead"] == "claude"
        assert task["revision"] == 1
        states = ["in_progress", "blocked", "in_progress", "review", "in_progress", "review", "done"]
        for revision, state in enumerate(states, start=2):
            task = store.set_status(task["id"], to=state, actor="claude")
            assert task["status"] == state and task["revision"] == revision
            assert datetime.fromisoformat(task["updated_at"]).utcoffset().total_seconds() == 0
        events = store.events(task["id"])
        assert len(events) == task["revision"]
        assert events[0]["type"] == "opened"
        assert all(event["type"] == "status" for event in events[1:])
        assert task["created_at"] <= task["updated_at"]
        assert store.board() == []
        assert_schema_valid("task.schema.json", task)


def test_ledger_invalid_and_terminal_transitions_are_atomic(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        for target in ("open", "blocked", "review", "done", "unknown"):
            before = _rows(store)
            _error(lambda target=target: store.set_status(task, to=target, actor="claude"), "Invalid task transition")
            assert _rows(store) == before
        for terminal in ("done", "cancelled"):
            current = _open(store, tmp_path)["id"]
            if terminal == "done":
                store.set_status(current, to="in_progress", actor="claude")
                store.set_status(current, to="review", actor="claude")
            store.set_status(current, to=terminal, actor="claude")
            for target in ("open", "in_progress", "blocked", "review", "done", "cancelled"):
                before = _rows(store)
                _error(
                    lambda current=current, target=target: store.set_status(current, to=target, actor="claude"),
                    "Invalid task transition",
                )
                _error(lambda current=current: _assign(store, current), "terminal task")
                assert _rows(store) == before


def test_ledger_cancel_from_every_nonterminal_state(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        for state in ("open", "in_progress", "blocked", "review"):
            task = _open(store, tmp_path)["id"]
            if state != "open":
                store.set_status(task, to="in_progress", actor="claude")
            if state in ("blocked", "review"):
                store.set_status(task, to=state, actor="claude")
            assert store.set_status(task, to="cancelled", actor="claude")["status"] == "cancelled"


def test_ledger_claim_conflicts_equal_ancestor_descendant_and_atomicity(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        first = _open(store, tmp_path)["id"]
        holder = _assign(store, first, ["src/hipson"])
        for scope in (["src/hipson"], ["src"], ["src/hipson/ledger.py"], ["tests", "src"], ["."]):
            task = _open(store, tmp_path)["id"]
            before = _rows(store)
            _error(lambda task=task, scope=scope: _assign(store, task, scope), holder["id"], ClaimConflictError)
            after = _rows(store)
            assert before["assignments"] == after["assignments"]
            assert before["claims"] == after["claims"]
            assert store.show(task)["status"] == "open"
            assert store.show(task)["revision"] == 2
            assert store.show(first)["revision"] == 2
            assert len(after["events"]) == len(before["events"]) + 1
            event = store.events(task)[-1]
            assert event["type"] == "claim_conflict" and event["actor"] == "codex"
            assert holder["id"] in event["summary"] and holder["id"] in event["refs"]
            assert store.show(task)["updated_at"] == event["timestamp"]


def test_ledger_disjoint_read_and_other_project_claims(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path / "one")["id"]
        _assign(store, task, ["src", "docs"])
        assert store.show(task)["status"] == "in_progress"
        _assign(store, task, ["src-other", "tests"])
        readonly = _assign(store, task, ["."], mode="ro", actor="agy")
        assert all(claim["assignment"] != readonly["id"] for claim in store.show(task)["claims"])
        assert len(store.show(task)["claims"]) == 4
        other = _open(store, tmp_path / "two")["id"]
        _assign(store, other, ["src"])
        assert len(store.show(other)["claims"]) == 1
        _error(lambda: _assign(store, other, []), "needs at least one scope path")
        empty = _assign(store, other, [], mode="ro", actor="agy")
        assert empty["scope"] == [] and len(store.show(other)["claims"]) == 1


def test_ledger_normalizes_and_rejects_unsafe_scope(tmp_path: Path):
    assert normalize_scope(["./src//hipson/", "src\\hipson", "tests/./a.py"]) == ["src/hipson", "tests/a.py"]
    assert normalize_scope([".", "./"]) == ["."]
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        for path in (
            "",
            "/src",
            "../src",
            "src/../tests",
            "src/..",
            "C:\\src",
            "C:src",
            "\\\\host\\share",
            "src\x00bad",
            "src\\..\\x",
        ):
            before = _rows(store)
            _error(lambda path=path: _assign(store, task, [path]), "relative repository paths")
            assert _rows(store) == before
        for path in (" ", "src ", " src", "src /module.py", "src/ a.py"):
            before = _rows(store)
            _error(lambda path=path: _assign(store, task, [path]), "must not start or end with whitespace")
            assert _rows(store) == before
        assert normalize_scope(["my dir/a b.py"]) == ["my dir/a b.py"]
        assigned = _assign(store, task, ["./src//hipson/", "src\\hipson"])
        assert assigned["scope"] == ["src/hipson"]
        assert store.show(task)["claims"] == [{"assignment": assigned["id"], "path": "src/hipson"}]


def test_ledger_finish_releases_claims_for_all_results(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        for result in ("passed", "failed", "cancelled"):
            assigned = _assign(store, task, ["src", "tests"], actor="agy")
            finished = store.finish(assigned["id"], result=result, summary="Finished", refs=["report.md"])
            assert finished["status"] == result and finished["ended_at"] >= finished["started_at"]
            assert store.show(task)["claims"] == []
            event = store.events(task)[-1]
            assert event["type"] == "finished" and event["actor"] == "agy"
            assert event["refs"] == [assigned["id"], "report.md"] and result in event["summary"]
            before = _rows(store)
            _error(
                lambda assigned=assigned, result=result: store.finish(assigned["id"], result=result, summary="Again"),
                "already finished",
            )
            assert before == _rows(store)
        assert store.show(task)["revision"] == 7


def test_ledger_events_order_cursor_and_task_isolation(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        first = _open(store, tmp_path)["id"]
        other = _open(store, tmp_path)["id"]
        for type in ("note", "progress", "blocked", "result"):
            event = store.add_event(
                first, actor="claude-subagent", type=type, summary="Recorded", refs=["report.md", other]
            )
            assert_schema_valid("task-event.schema.json", event)
            store.add_event(other, actor="claude", type="note", summary="Other task")
        events = store.events(first)
        seqs = [event["seq"] for event in events]
        assert seqs == sorted(set(seqs)) and len(seqs) == 5
        assert all(event["task"] == first for event in events)
        assert store.events(first, after=seqs[1]) == events[2:]
        assert store.events(first, after=seqs[-1]) == []
        assert store.events(first, after=1_000_000) == []
        assert store.show(first)["status"] == "open"
        assert store.show(first)["revision"] == len(events)
        _error(lambda: store.events(first, after=-1), "between 0 and")
        _error(lambda: store.events(first, after=2**63), "between 0 and")
        _error(lambda: store.events("T-9999"), "Task not found")


def test_ledger_redacts_before_persistence(tmp_path: Path):
    secret = "sk-test-secret1234567890"
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = store.open_task(
            project=tmp_path, title=f"Title {secret}", objective=f"Objective {secret}", acceptance=[f"api_key={secret}"]
        )["id"]
        assigned = _assign(store, task)
        store.add_event(task, actor="codex", type="progress", summary=f"token={secret}")
        store.set_status(task, to="review", actor="claude", summary=f"Review {secret}")
        store.finish(assigned["id"], result="passed", summary=f"Finished {secret}")
        raw = json.dumps(_rows(store))
        assert secret not in raw and REDACTION in raw
        view = store.show(task)
        assert REDACTION in view["title"] and REDACTION in view["objective"]
        assert REDACTION in view["acceptance"][0]
        assert REDACTION in store.events(task)[-1]["summary"]
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        assert secret not in json.dumps(_rows(store))


def test_ledger_mutations_roll_back_when_event_insert_fails(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        assigned = _assign(store, task)
        store.connection.execute("""CREATE TEMP TRIGGER fail_event BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'injected event failure'); END""")
        operations = (
            lambda: _open(store, tmp_path),
            lambda: _assign(store, task, ["tests"]),
            lambda: _assign(store, task, ["src"]),
            lambda: store.finish(assigned["id"], result="passed", summary="Finished"),
            lambda: store.set_status(task, to="review", actor="claude"),
            lambda: store.add_event(task, actor="claude", type="note", summary="Note"),
        )
        for operation in operations:
            before = _rows(store)
            _error(operation, "injected event failure", sqlite3.IntegrityError)
            assert _rows(store) == before
        store.connection.execute("DROP TRIGGER fail_event")
        assert store.finish(assigned["id"], result="passed", summary="Retry")["status"] == "passed"


def test_ledger_schema_version_wal_and_append_only_events(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
        assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 5
        for sql in ("UPDATE events SET summary = 'changed'", "DELETE FROM events"):
            _error(lambda sql=sql: store.connection.execute(sql), "append-only", sqlite3.IntegrityError)
        assert len(store.events(task)) == 1 and store.show(task)["revision"] == 1
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE schema_version SET version = 999")
        connection.commit()
    _error(lambda: Ledger(db), "Unsupported task ledger schema version 999")


def test_ledger_list_board_and_terminal_cleanup(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path / "one")["id"]
        assigned = _assign(store, task)
        read = _assign(store, task, ["."], mode="ro")
        store.finish(read["id"], result="passed", summary="Read done")
        store.ack(read["id"])
        other = _open(store, tmp_path / "two")["id"]
        assert [item["id"] for item in store.list_tasks(project=tmp_path / "one" / ".")] == [task]
        assert [item["id"] for item in store.list_tasks(status="open")] == [other]
        assert len(store.list_tasks()) == 2
        assert [item["id"] for item in store.board()[0]["assignments"]] == [assigned["id"]]
        assert len(store.show(task)["assignments"]) == 2
        store.set_status(task, to="cancelled", actor="claude")
        assert [item["id"] for item in store.board()] == [task, other]
        store.finish(assigned["id"], result="cancelled", summary="Cleanup")
        assert store.show(task)["claims"] == []
        assert store.add_event(task, actor="claude", type="note", summary="Post-terminal audit")["type"] == "note"


def test_ledger_invalid_inputs_do_not_write(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        operations = (
            (lambda: store.open_task(project=tmp_path, title=" ", objective="Objective"), "title must not be empty"),
            (lambda: store.assign(task, actor=" ", role="builder", mode="rw"), "actor must not be empty"),
            (lambda: store.assign(task, actor="codex", role="invalid", mode="rw"), "Assignment requires"),
            (lambda: store.assign(task, actor="codex", role="builder", mode="invalid"), "Assignment requires"),
            (lambda: store.assign(task, actor="codex", role="builder", mode="rw"), "needs at least one scope path"),
            (lambda: _assign(store, "T-9999"), "Task not found"),
            (lambda: store.finish("A-9999", result="passed", summary="Finished"), "Assignment not found"),
            (lambda: store.finish("A-9999", result="invalid", summary="Finished"), "Assignment result"),
            (lambda: store.add_event(task, actor="claude", type="note", summary=" "), "summary must not be empty"),
            (lambda: store.add_event("T-9999", actor="claude", type="note", summary="Note"), "Task not found"),
        )
        for operation, message in operations:
            before = _rows(store)
            _error(operation, message)
            assert _rows(store) == before


def test_ledger_db_path_precedence(tmp_path: Path):
    with patch.dict(os.environ, {"HIPSON_HOME": str(tmp_path / "home")}, clear=True):
        assert default_ledger_db() == tmp_path / "home" / "ledger.sqlite"
        with patch.dict(os.environ, {"HIPSON_LEDGER": str(tmp_path / "env.sqlite")}):
            with closing(Ledger()) as store:
                assert store.path == tmp_path / "env.sqlite"
            with closing(Ledger(tmp_path / "explicit.sqlite")) as store:
                assert store.path == tmp_path / "explicit.sqlite"
        assert not (tmp_path / "home").exists()


def test_ledger_two_processes_racing_for_overlapping_claims(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        tasks = [_open(store, tmp_path)["id"] for _ in range(2)]
    script = """
import sys
from contextlib import closing
from hipson.ledger import Ledger, ClaimConflictError
with closing(Ledger(sys.argv[1])) as store:
    def trace(sql):
        if sql == 'BEGIN IMMEDIATE':
            print('contending', flush=True)
    store.connection.set_trace_callback(trace)
    print('ready', flush=True)
    sys.stdin.readline()
    try:
        item = store.assign(sys.argv[2], actor='codex', role='builder', mode='rw', scope=[sys.argv[3]])
    except ClaimConflictError:
        print('conflict')
    else:
        print('ok:' + item['id'])
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    processes = []
    try:
        for task, scope in zip(tasks, ("src", "src/hipson"), strict=True):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", script, str(db), task, scope],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
            )
        assert all(_readline(process) == "ready" for process in processes)
        # Hold a writer lock until BOTH independent processes are attempting
        # BEGIN IMMEDIATE. This proves contention rather than serial execution.
        with closing(Ledger(db)) as blocker:
            blocker.connection.execute("BEGIN IMMEDIATE")
            for process in processes:
                process.stdin.write("start\n")
                process.stdin.flush()
            assert all(_readline(process) == "contending" for process in processes)
            blocker.connection.rollback()
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            outputs.append(stdout.strip())
        assert sum(output.startswith("ok:") for output in outputs) == 1, outputs
        assert outputs.count("conflict") == 1, outputs
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
    with closing(Ledger(db)) as store:
        assert store.connection.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
        events = [event for task in tasks for event in store.events(task)]
        assert sorted(event["type"] for event in events) == ["assigned", "claim_conflict", "opened", "opened"]
        assert all(store.show(task)["revision"] == 2 for task in tasks)


def test_ledger_cli_json_each_subcommand(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"

    def command(name, *args):
        result = run_cli(tmp_path, "task", name, *args, "--db", str(db), "--json")
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    task = command(
        "open",
        "--project",
        ".",
        "--title",
        "Ledger",
        "--objective",
        "Track work",
        "--acceptance",
        "First",
        "--acceptance",
        "Second",
    )
    assert task["acceptance"] == ["First", "Second"]
    assert task["project"] == str(tmp_path.resolve()) and task["lead"] == "claude"
    task_id = task["id"]
    assigned = command(
        "assign",
        "--task",
        task_id,
        "--actor",
        "codex",
        "--role",
        "builder",
        "--mode",
        "rw",
        "--scope",
        "src",
        "--scope",
        "tests",
    )
    event = command(
        "event",
        "--task",
        task_id,
        "--actor",
        "codex",
        "--type",
        "progress",
        "--summary",
        "Ready",
        "--ref",
        assigned["id"],
        "--ref",
        "report.md",
    )
    assert event["refs"] == [assigned["id"], "report.md"]
    assert command("show", "--task", task_id)["assignments"] == [assigned]
    assert command("board")[0]["assignments"] == [assigned]
    assert command("list", "--project", ".", "--status", "in_progress")[0]["id"] == task_id
    assert (
        command(
            "finish",
            "--assignment",
            assigned["id"],
            "--result",
            "passed",
            "--summary",
            "Tests pass",
            "--ref",
            "report.md",
        )["status"]
        == "passed"
    )
    status = command("status", "--task", task_id, "--to", "review", "--actor", "claude")
    assert status["status"] == "review" and status["claims"] == []
    events = command("events", "--task", task_id, "--after", str(event["seq"]))
    assert [item["type"] for item in events] == ["finished", "status"]
    assert_schema_valid("task.schema.json", status)
    assert_schema_valid("task-event.schema.json", event)
    assert_strict_schema("task.schema.json", status)
    assert_strict_schema("task-event.schema.json", event)
    assert contracts.ARTIFACT_SCHEMAS["hipson.task"] == "schemas/task.schema.json"
    assert contracts.ARTIFACT_SCHEMAS["hipson.task_event"] == "schemas/task-event.schema.json"


def test_ledger_cli_errors_and_board_text(tmp_path: Path):
    db = str(tmp_path / "ledger.sqlite")
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        holder = _assign(store, task)["id"]
    rc, stdout, stderr = run_cli_inprocess(
        "task",
        "assign",
        "--db",
        db,
        "--json",
        "--task",
        task,
        "--actor",
        "agy",
        "--role",
        "builder",
        "--mode",
        "rw",
        "--scope",
        "src/hipson",
    )
    assert rc == 1 and stdout == "" and holder in stderr
    rc, stdout, stderr = run_cli_inprocess("task", "--db", db, "--json", "events", "--task", task)
    assert rc == 0 and json.loads(stdout)[-1]["type"] == "claim_conflict"
    rc, stdout, stderr = run_cli_inprocess("task", "--db", db, "board")
    assert rc == 0 and task in stdout and holder in stdout and "codex:builder/rw" in stdout
    for args in (
        ("status", "--task", task, "--actor", "claude", "--to", "done"),
        ("show", "--task", "T-9999"),
        ("events", "--task", task, "--after", "-1"),
    ):
        rc, stdout, stderr = run_cli_inprocess("task", "--db", db, *args, "--json")
        assert rc == 1 and stderr and not stdout
    rc, _, _ = run_cli_inprocess(
        "task", "assign", "--db", db, "--task", task, "--actor", "agy", "--role", "invalid", "--mode", "ro"
    )
    assert rc == 2
    rc, _, stderr = run_cli_inprocess("task", "board", "--db", str(tmp_path))
    assert rc == 1 and stderr
    rc, stdout, _ = run_cli_inprocess("task", "board", "--db", str(tmp_path / "empty.sqlite"))
    assert rc == 0 and stdout.strip() == "No active tasks."


def test_ledger_read_only_reads_survive_concurrent_writes(tmp_path: Path):
    """Reads racing a writer retry internally instead of surfacing 'changed while opening'."""
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
    stop = False
    errors: list[str] = []

    def writer() -> None:
        # One connection per write, like separate CLI invocations: WAL sidecars appear and vanish.
        for index in range(150):
            with closing(Ledger(db)) as store:
                store.add_event(task, actor="codex", type="progress", summary=f"step {index}")

    def reader() -> None:
        while not stop:
            try:
                with closing(Ledger(db, read_only=True)) as store:
                    store.show(task)
            except LedgerError as exc:
                errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader)
        try:
            pool.submit(writer).result(timeout=60)
        finally:
            stop = True
        reading.result(timeout=60)
    assert errors == []
