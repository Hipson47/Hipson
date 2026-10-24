"""Orphan recovery acceptance tests, also supported by scripts/run_tests.py."""

import ctypes
import errno
import json
import os
import signal
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hipson import ledger
from hipson.ledger import ClaimConflictError, Ledger
from tests.test_hipson_helpers import run_cli, run_cli_inprocess
from tests.test_ledger import SCHEMAS, _assign, _error, _open, _readline, _rows, _strict_errors, assert_strict_schema
from tests.test_ledger_mcp import WRITE_ARGUMENTS, _assert_assignment, _assert_error, _call, _payload

OWNER = {"owner_pid": 42, "owner_boot_id": "boot-current", "owner_host": "local"}


def _store(db, *, alive=False, boot="boot-current", **kwargs):
    return Ledger(db, hostname=lambda: "local", boot_id=lambda: boot, pid_alive=lambda pid: alive, **kwargs)


def _v1_file(db):
    fixture = Path(__file__).parent / "fixtures" / "task-ledger-v1.sql"
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
        connection.commit()


def test_ledger_migrates_real_v1_file_preserving_all_rows_and_events(tmp_path: Path):
    db = tmp_path / "v1.sqlite"
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        before = _rows(SimpleNamespace(connection=connection))
    with closing(Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        after = _rows(store)
        assert len(after["tasks"]) == 2 and len(after["assignments"]) == 3 and len(after["events"]) == 6
        for table in ("tasks", "claims", "events"):
            assert after[table] == before[table]
        assert [row[:-4] for row in after["assignments"]] == before["assignments"]
        assert all(row[-4:] == (None, None, None, None) for row in after["assignments"])
        for task in store.list_tasks():
            assert_strict_schema("task.schema.json", store.show(task["id"]))
        assert store.reap() == []  # Migrated owner-less claims remain held.
        _error(lambda: _assign(store, "T-0001"), "A-0001", ClaimConflictError)
        _error(lambda: store.connection.execute("DELETE FROM events"), "append-only", sqlite3.IntegrityError)
        store.finish("A-0001", result="passed", summary="Legacy writer done")
        new = _assign(store, "T-0001", owner_pid=42, owner_host="remote", lease_seconds=3600)
        assert new["id"] == "A-0004" and new["owner_pid"] == 42
        assert store.events("T-0001")[-1]["seq"] > 6
    with closing(Ledger(db)) as store:
        assert _rows(store)["assignments"][-1][-4] == 42  # Reopening never repeats ALTER TABLE.


def test_ledger_v1_migration_rolls_back_ddl_and_version_on_failure(tmp_path: Path):
    db = tmp_path / "v1.sqlite"
    _v1_file(db)
    statements = (*ledger.MIGRATION_V1_TO_V2[:2], "SELECT * FROM injected_missing_table")
    with patch.object(ledger, "MIGRATION_V1_TO_V2", statements):
        _error(lambda: Ledger(db), "injected_missing_table", sqlite3.OperationalError)
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert len(connection.execute("PRAGMA table_info(assignments)").fetchall()) == 9
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 6
    with closing(Ledger(db)) as store:
        assert len(store.show("T-0001")["assignments"]) == 2


def test_ledger_reap_reasons_and_conservative_liveness(tmp_path: Path):
    with closing(_store(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        cases = [
            ({"lease_seconds": 0}, "lease_expired"),
            ({"owner_host": "local", "owner_boot_id": "boot-old"}, "boot_id_changed"),
            (OWNER, "pid_gone"),
            ({**OWNER, "owner_host": "remote"}, None),
            ({}, None),
            ({"owner_pid": 42}, None),
            ({"owner_host": "local"}, None),
            ({"owner_host": "local", "owner_boot_id": "boot-current"}, None),
            ({"lease_seconds": 3600}, None),
            ({**OWNER, "owner_host": "remote", "lease_seconds": 0}, "lease_expired"),
            ({**OWNER, "owner_boot_id": "boot-old"}, "boot_id_changed"),
            ({"owner_pid": 42, "owner_host": "local"}, "pid_gone"),
        ]
        assignments = [_assign(store, task, mode="ro", **owner) for owner, _ in cases]
        before = _rows(store)
        expected = [(item["id"], reason) for item, (_, reason) in zip(assignments, cases, strict=True) if reason]
        preview = store.reap(task=task, dry_run=True)
        assert [(item["assignment"]["id"], item["reason"]) for item in preview] == expected
        assert all(item["assignment"]["status"] == "active" for item in preview)
        assert _rows(store) == before
        result = store.reap(task=task)
        assert [(item["assignment"]["id"], item["reason"]) for item in result] == expected
        assert all(item["assignment"]["status"] == "cancelled" for item in result)
        assert all(item["assignment"]["ended_at"] for item in result)
        events = store.events(task)[-len(expected):]
        assert all(event["type"] == "reaped" for event in events)
        for event, (assignment, reason) in zip(events, expected, strict=True):
            assert event["refs"] == [assignment] and reason in event["summary"]
            assert_strict_schema("task-event.schema.json", event)
        assert store.show(task)["revision"] == 1 + len(cases) + len(expected)
        assert_strict_schema("task.schema.json", store.show(task))
        assert store.reap() == []  # Reaping is idempotent.


def test_ledger_reap_keeps_live_pid_and_unknown_boot(tmp_path: Path):
    with closing(_store(tmp_path / "live.sqlite", alive=True)) as store:
        task = _open(store, tmp_path)["id"]
        holder = _assign(store, task, **OWNER)
        assert store.reap() == []
        _error(lambda: _assign(store, task), holder["id"], ClaimConflictError)
        assert store.show(task)["assignments"] == [holder]
        assert store.events(task)[-1]["type"] == "claim_conflict"
    with closing(_store(tmp_path / "unknown-boot.sqlite", alive=True, boot=None)) as store:
        task = _open(store, tmp_path)["id"]
        _assign(store, task, **OWNER)
        assert store.reap() == []


def test_ledger_pid_probe_requires_both_pid_and_group_gone():
    gone = ProcessLookupError(errno.ESRCH, "gone")
    for pid_outcome in (None, gone, PermissionError(errno.EPERM, "denied"), OSError(errno.EIO, "unknown")):
        for group_outcome in (None, gone, PermissionError(errno.EPERM, "denied"), OSError(errno.EIO, "unknown")):
            with (
                patch.object(ledger.os, "kill", side_effect=pid_outcome) as kill,
                patch.object(ledger.os, "killpg", side_effect=group_outcome) as killpg,
            ):
                assert ledger._pid_alive(42) is not (pid_outcome is gone and group_outcome is gone)
                kill.assert_called_once_with(42, 0)
                if pid_outcome is gone:
                    killpg.assert_called_once_with(42, 0)
                else:
                    killpg.assert_not_called()


def test_ledger_reap_filters_task_and_commits_each_orphan_atomically(tmp_path: Path):
    with closing(_store(tmp_path / "ledger.sqlite")) as store:
        first, second = [_open(store, tmp_path)["id"] for _ in range(2)]
        holder = _assign(store, first, ["src", "tests"], **OWNER)
        other = _assign(store, second, ["docs"], **OWNER)
        before = _rows(store)
        _error(lambda: store.reap(task="T-9999"), "Task not found")
        assert _rows(store) == before
        assert [item["assignment"]["id"] for item in store.reap(task=first, dry_run=True)] == [holder["id"]]
        store.connection.execute(f"""CREATE TEMP TRIGGER fail_second BEFORE INSERT ON events
            WHEN NEW.type = 'reaped' AND NEW.task = '{second}'
            BEGIN SELECT RAISE(ABORT, 'injected reaping failure'); END""")
        _error(lambda: store.reap(), "injected reaping failure", sqlite3.IntegrityError)
        assert store.show(first)["claims"] == []
        assert store.show(first)["assignments"][0]["status"] == "cancelled"
        assert store.events(first)[-1]["type"] == "reaped"
        assert store.show(second)["assignments"] == [other]
        assert store.show(second)["claims"] == [{"assignment": other["id"], "path": "docs"}]
        assert store.events(second)[-1]["type"] == "assigned"
        store.connection.execute("DROP TRIGGER fail_second")
        assert len(store.reap(task=second)) == 1


def test_ledger_rw_assign_recovers_only_overlapping_claims_in_same_project(tmp_path: Path):
    with closing(_store(tmp_path / "ledger.sqlite")) as store:
        first = _open(store, tmp_path)["id"]
        holder = _assign(store, first, ["src", "tests"], **OWNER)
        disjoint = _assign(store, first, ["docs"], **OWNER)
        reader = _assign(store, first, mode="ro", lease_seconds=0)
        other_task = _open(store, tmp_path / "other")["id"]
        other = _assign(store, other_task, **OWNER)
        task = _open(store, tmp_path)["id"]
        cursor = store.events(task)[-1]["seq"]
        assigned = _assign(store, task, ["src/hipson"])
        assert store.show(first)["assignments"][0]["status"] == "cancelled"
        assert store.show(first)["assignments"][1:] == [disjoint, reader]
        assert store.show(first)["claims"] == [{"assignment": disjoint["id"], "path": "docs"}]
        assert store.show(other_task)["assignments"] == [other]
        events = store.connection.execute("SELECT type, refs FROM events WHERE seq > ? ORDER BY seq", (cursor,)).fetchall()
        assert [row["type"] for row in events] == ["reaped", "assigned"]
        assert json.loads(events[0]["refs"]) == [holder["id"]]
        assert store.show(task)["claims"] == [{"assignment": assigned["id"], "path": "src/hipson"}]
        # The whole holder is cancelled, so its second claim is also available.
        assert _assign(store, task, ["tests"])["status"] == "active"


def test_ledger_rw_recovery_and_assignment_roll_back_together(tmp_path: Path):
    with closing(_store(tmp_path / "ledger.sqlite")) as store:
        task = _open(store, tmp_path)["id"]
        _assign(store, task, **OWNER)
        before = _rows(store)
        store.connection.execute("""CREATE TEMP TRIGGER fail_assignment BEFORE INSERT ON events
            WHEN NEW.type = 'assigned'
            BEGIN SELECT RAISE(ABORT, 'injected assignment failure'); END""")
        _error(lambda: _assign(store, task), "injected assignment failure", sqlite3.IntegrityError)
        assert _rows(store) == before


def test_ledger_rw_recovery_preserves_live_conflict_and_commits_reaped_event(tmp_path: Path):
    with closing(Ledger(tmp_path / "ledger.sqlite", hostname=lambda: "local",
                        boot_id=lambda: "boot-current", pid_alive=lambda pid: pid == 43)) as store:
        task = _open(store, tmp_path)["id"]
        dead = _assign(store, task, ["src"], **OWNER)
        live = _assign(store, task, ["tests"], **{**OWNER, "owner_pid": 43})
        _error(lambda: _assign(store, task, ["."]), live["id"], ClaimConflictError)
        view = store.show(task)
        assert view["assignments"][0]["id"] == dead["id"] and view["assignments"][0]["status"] == "cancelled"
        assert view["assignments"][1] == live
        assert view["claims"] == [{"assignment": live["id"], "path": "tests"}]
        assert [event["type"] for event in store.events(task)][-2:] == ["reaped", "claim_conflict"]


def test_ledger_owner_validation_and_strict_schema(tmp_path: Path):
    with closing(_store(tmp_path / "ledger.sqlite", alive=True)) as store:
        task = _open(store, tmp_path)["id"]
        for arguments in (
            {"owner_pid": 0}, {"owner_pid": -1}, {"owner_pid": True}, {"owner_pid": "42"},
            {"owner_pid": 2**31}, {"owner_host": " "}, {"owner_boot_id": ""},
            {"lease_seconds": -1}, {"lease_seconds": True}, {"lease_seconds": 0.5}, {"lease_seconds": 2**63},
        ):
            before = _rows(store)
            _error(lambda arguments=arguments: _assign(store, task, **arguments), next(iter(arguments)))
            assert _rows(store) == before
        item = _assign(store, task, **OWNER, lease_seconds=60)
        assert (datetime.fromisoformat(item["lease_until"]) - datetime.fromisoformat(item["started_at"])).total_seconds() == 60
        view = store.show(task)
        assert_strict_schema("task.schema.json", view)
        root = json.loads((SCHEMAS / "task.schema.json").read_text(encoding="utf-8"))
        schema = root["$defs"]["assignment"]
        for field, value in (
            ("owner_pid", True), ("owner_pid", "42"), ("owner_pid", -1), ("owner_pid", 2**31),
            ("owner_boot_id", 1), ("owner_host", []), ("lease_until", "not a timestamp"),
        ):
            assert _strict_errors(schema, {**item, field: value}, root), (field, value)
        legacy = {key: value for key, value in item.items() if key not in (*OWNER, "lease_until")}
        assert not _strict_errors(schema, legacy, root)  # Owner fields are optional in the artifact contract.


def test_ledger_cli_owner_fields_and_reap_dry_run_write_nothing(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(_store(db)) as store:
        task = _open(store, tmp_path)["id"]
    rc, stdout, stderr = run_cli_inprocess(
        "task", "assign", "--db", str(db), "--json", "--task", task, "--actor", "codex",
        "--role", "builder", "--mode", "rw", "--scope", "src", "--owner-pid", "42",
        "--owner-boot-id", "boot-old", "--owner-host", "remote", "--lease-seconds", "0",
    )
    assert rc == 0, stderr
    assigned = json.loads(stdout)
    assert assigned["owner_pid"] == 42 and assigned["owner_boot_id"] == "boot-old" and assigned["owner_host"] == "remote"
    with closing(_store(db)) as store:
        before = _rows(store)
    original = db.read_bytes()
    rc, stdout, stderr = run_cli_inprocess("task", "reap", "--db", str(db), "--task", task, "--dry-run", "--json")
    assert rc == 0, stderr
    preview = json.loads(stdout)
    assert preview == [{"assignment": assigned, "reason": "lease_expired"}]
    assert db.read_bytes() == original
    with closing(_store(db)) as store:
        assert _rows(store) == before
    rc, stdout, stderr = run_cli_inprocess("task", "reap", "--db", str(db), "--task", task, "--json")
    assert rc == 0, stderr
    result = json.loads(stdout)
    assert result[0]["reason"] == "lease_expired" and result[0]["assignment"]["status"] == "cancelled"
    missing = tmp_path / "missing" / "ledger.sqlite"
    rc, stdout, stderr = run_cli_inprocess("task", "reap", "--db", str(missing), "--dry-run", "--json")
    assert rc == 0 and json.loads(stdout) == [], stderr
    assert not missing.parent.exists()


def test_ledger_cli_and_mcp_reads_do_not_migrate_v1(tmp_path: Path):
    db = tmp_path / "v1.sqlite"
    _v1_file(db)
    original = db.read_bytes()
    for command in ("show", "events", "list", "board", "reap"):
        cli_arguments = ["--task", "T-0001"] if command in ("show", "events") else []
        mcp_arguments = {"task": "T-0001"} if cli_arguments else {}
        if command == "reap":
            cli_arguments = ["--dry-run"]
            mcp_arguments = {"approved": True, "dry_run": True}
        rc, stdout, stderr = run_cli_inprocess("task", command, "--db", str(db), "--json", *cli_arguments)
        assert rc == 0, stderr
        cli_payload = json.loads(stdout)
        # Ordinary MCP reads have no approval: they must not migrate implicitly.
        assert _payload(_call(tmp_path, f"task.{command}", ledger_db=db, **mcp_arguments)) == cli_payload
        assert db.read_bytes() == original
        assert sorted(path.name for path in tmp_path.iterdir()) == ["v1.sqlite"]
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == 1


def test_ledger_cli_missing_store_reads_create_nothing(tmp_path: Path):
    db = tmp_path / "missing" / "ledger.sqlite"
    for command in ("show", "events", "list", "board", "reap"):
        for json_args in ([], ["--json"]):
            arguments = ["--task", "T-0001"] if command in ("show", "events") else []
            if command == "reap":
                arguments = ["--dry-run"]
            rc, stdout, stderr = run_cli_inprocess("task", command, "--db", str(db), *arguments, *json_args)
            if command in ("show", "events"):
                assert rc == 1 and "Task not found: T-0001" in stderr
            else:
                assert rc == 0, stderr
                assert stdout.strip() == ("No active tasks." if command == "board" and not json_args else "[]")
            assert not db.parent.exists()
    rc, stdout, stderr = run_cli_inprocess("task", "reap", "--db", str(db), "--task", "T-0001", "--dry-run")
    assert rc == 1 and "Task not found: T-0001" in stderr
    assert not db.parent.exists()


def test_ledger_read_only_v2_directory_with_live_and_checkpointed_wal(tmp_path: Path):
    parent = tmp_path / "readonly"
    db = parent / "ledger.sqlite"

    def check_reads():
        original = {path.name: path.read_bytes() for path in parent.iterdir() if not path.name.endswith("-shm")}
        names = sorted(path.name for path in parent.iterdir())
        modes = {path: path.stat().st_mode & 0o777 for path in parent.iterdir()}
        for path in modes:
            path.chmod(0o444)
        parent.chmod(0o555)
        try:
            for command in ("show", "events", "list", "board", "reap"):
                arguments = ["--task", task] if command in ("show", "events") else []
                mcp_arguments = {"task": task} if arguments else {}
                if command == "reap":
                    arguments = ["--dry-run"]
                    mcp_arguments = {"approved": True, "dry_run": True}
                # A fresh process cannot reuse the writer's writable SHM mapping.
                result = run_cli(tmp_path, "task", command, "--db", str(db), "--json", *arguments)
                assert result.returncode == 0, result.stderr
                assert _payload(_call(tmp_path, f"task.{command}", ledger_db=db, **mcp_arguments)) == json.loads(
                    result.stdout
                )
            with closing(Ledger(db, read_only=True)) as reader:
                _error(lambda: reader.connection.execute("DELETE FROM claims"), "readonly", sqlite3.OperationalError)
            assert sorted(path.name for path in parent.iterdir()) == names
            assert {name: (parent / name).read_bytes() for name in original} == original
        finally:
            parent.chmod(0o755)
            for path, mode in modes.items():
                path.chmod(mode)

    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
        _assign(store, task, lease_seconds=0)
        assert Path(f"{db}-wal").stat().st_size > 0
        check_reads()  # Include committed rows that still reside in WAL.
    assert list(parent.iterdir()) == [db]
    check_reads()  # WAL-mode header remains, but no sidecars may be created.


def test_ledger_read_only_wal_without_shm_fails_without_creating_files(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        _open(store, tmp_path)
        copy = tmp_path / "copy.sqlite"
        copy.write_bytes(db.read_bytes())
        Path(f"{copy}-wal").write_bytes(Path(f"{db}-wal").read_bytes())
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        rc, stdout, stderr = run_cli_inprocess("task", "board", "--db", str(copy), "--json")
        assert rc == 1 and "WAL without a readable -shm file" in stderr
        _assert_error(_call(tmp_path, "task.board", ledger_db=copy), "WAL without a readable -shm file")
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_ledger_read_only_wal_survives_last_writer_closing_during_open(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    writer = Ledger(db)
    task = _open(writer, tmp_path)
    connect = sqlite3.connect
    original_names = sorted(path.name for path in tmp_path.iterdir())

    def close_writer_before_connect(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("&readonly_shm=1"):
            writer.close()
        return connect(*args, **kwargs)

    try:
        with patch.object(ledger.sqlite3, "connect", side_effect=close_writer_before_connect), closing(
            Ledger(db, read_only=True)
        ) as reader:
            assert reader.show(task["id"]) == task
        assert sorted(path.name for path in tmp_path.iterdir()) == original_names
    finally:
        writer.close()


def test_ledger_read_only_snapshot_retries_when_a_writer_changes_the_source(tmp_path: Path):
    connect = sqlite3.connect
    for keep_wal in (False, True):
        db = tmp_path / f"concurrent-{keep_wal}.sqlite"
        with closing(Ledger(db)) as store:
            _open(store, tmp_path)
        writer = None
        written = None

        def connect_and_write(*args, active_db=db, retain_wal=keep_wal, **kwargs):
            source = connect(*args, **kwargs)
            if isinstance(args[0], str) and args[0].endswith("&immutable=1") and written is None:
                def backup(snapshot):
                    nonlocal writer, written
                    source.backup(snapshot)
                    writer = Ledger(active_db)
                    written = _open(writer, tmp_path, title="Concurrent commit")
                    if not retain_wal:
                        writer.close()
                        writer = None
                return SimpleNamespace(backup=backup, close=source.close)
            return source

        try:
            with patch.object(ledger.sqlite3, "connect", side_effect=connect_and_write), closing(
                Ledger(db, read_only=True)
            ) as reader:
                assert written is not None
                assert reader.show(written["id"])["title"] == "Concurrent commit"
        finally:
            if writer is not None:
                writer.close()


def test_ledger_mcp_reap_gate_owner_fields_and_strict_payloads(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    for dry_run in (False, True):
        for approval in ({}, {"approved": False}, {"approved": 1}, {"approved": "true"}):
            _assert_error(_call(tmp_path, "task.reap", ledger_db=db, dry_run=dry_run, **approval),
                          "task.reap requires approved=true")
    assert not db.exists()
    assert _payload(_call(tmp_path, "task.reap", ledger_db=db, approved=True, dry_run=True)) == []
    assert not db.exists()
    with closing(_store(db)) as store:
        task = _open(store, tmp_path)["id"]
    arguments = {**WRITE_ARGUMENTS["task.assign"], "task": task, **OWNER, "lease_seconds": 0, "approved": True}
    assigned = _payload(_call(tmp_path, "task.assign", ledger_db=db, **arguments))
    _assert_assignment(assigned)
    assert all(assigned[key] == value for key, value in OWNER.items())
    preview = _payload(_call(tmp_path, "task.reap", ledger_db=db, task=task, dry_run=True, approved=True))
    assert preview == [{"assignment": assigned, "reason": "lease_expired"}]
    reaped = _payload(_call(tmp_path, "task.reap", ledger_db=db, task=task, approved=True))
    _assert_assignment(reaped[0]["assignment"])
    assert reaped[0]["assignment"]["status"] == "cancelled" and reaped[0]["reason"] == "lease_expired"
    view = _payload(_call(tmp_path, "task.show", ledger_db=db, task=task))
    assert_strict_schema("task.schema.json", view)
    assert view["claims"] == []
    assert _payload(_call(tmp_path, "task.reap", ledger_db=db, approved=True)) == []


def test_ledger_mcp_owner_and_reap_argument_types_checked_before_open(tmp_path: Path):
    db = tmp_path / "missing.sqlite"
    for field, value in (
        ("owner_pid", True), ("owner_pid", 0), ("owner_pid", "42"), ("owner_pid", 2**31),
        ("owner_boot_id", None), ("owner_boot_id", ""), ("owner_host", 1),
        ("lease_seconds", -1), ("lease_seconds", 1.5), ("lease_seconds", True),
    ):
        _assert_error(_call(tmp_path, "task.assign", ledger_db=db, **{
            **WRITE_ARGUMENTS["task.assign"], field: value, "approved": True,
        }), "task.assign")
        assert not db.exists()
    for arguments in ({"dry_run": "true"}, {"task": None}, {"unexpected": 1}):
        _assert_error(_call(tmp_path, "task.reap", ledger_db=db, approved=True, **arguments), "task.reap")
        assert not db.exists()


def test_ledger_real_sigkill_holder_keeps_claims_until_worker_group_is_gone(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)["id"]
    script = """
import os
import socket
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from hipson.ledger import Ledger
child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with closing(Ledger(sys.argv[1])) as store:
    item = store.assign(sys.argv[2], actor='codex', role='builder', mode='rw', scope=['src'],
                        owner_pid=os.getpid(), owner_host=socket.gethostname(),
                        owner_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
print(item['id'], child.pid, os.getpgrp(), flush=True)
sys.stdin.readline()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    # Adopt/reap the orphaned grandchild so a container's PID 1 cannot leave a
    # zombie in the group and make killpg(..., 0) report it alive indefinitely.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # PR_GET_CHILD_SUBREAPER
    assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-c", script, str(db), task], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                   start_new_session=True)
        assignment, child, pgid = _readline(process).split()
        child_pid = int(child)
        assert assignment.startswith("A-") and int(pgid) == process.pid
        assert os.getpgid(child_pid) == process.pid
        with closing(Ledger(db)) as store:
            assert store.reap() == []
            _error(lambda: _assign(store, task), assignment, ClaimConflictError)
        process.kill()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL, stderr
        with closing(Ledger(db)) as store:
            assert store.reap() == []
            _error(lambda: _assign(store, task), assignment, ClaimConflictError)
            assert store.show(task)["claims"] == [{"assignment": assignment, "path": "src"}]
            os.kill(child_pid, signal.SIGKILL)
            assert os.waitpid(child_pid, 0)[1] == signal.SIGKILL
            reaped = store.reap()
            assert len(reaped) == 1
            assert reaped[0]["assignment"]["id"] == assignment and reaped[0]["reason"] == "pid_gone"
            assert store.show(task)["claims"] == []
            assert store.events(task)[-1]["type"] == "reaped"
            assert _assign(store, task)["status"] == "active"
    finally:
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=10)
                while True:
                    try:
                        os.waitpid(-process.pid, 0)
                    except ChildProcessError:
                        break
        finally:
            assert libc.prctl(36, previous.value, 0, 0, 0) == 0
