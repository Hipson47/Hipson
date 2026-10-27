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
from unittest import SkipTest
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
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 6
        after = _rows(store)
        assert len(after["tasks"]) == 2 and len(after["assignments"]) == 3 and len(after["events"]) == 6
        for table in ("tasks", "claims", "events"):
            assert after[table] == before[table]
        assert [row[:-10] for row in after["assignments"]] == before["assignments"]
        assert all(row[-10:-1] == (None,) * 9 for row in after['assignments'])
        assert [row[-1] for row in after['assignments']] == ['', ledger.LEGACY_ACK_NOTE, '']
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
        assert _rows(store)["assignments"][-1][-10] == 42  # Reopening never repeats ALTER TABLE.


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


def _v3_file(db):
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        for statement in (*ledger.MIGRATION_V1_TO_V2, *ledger.MIGRATION_V2_TO_V3):
            connection.execute(statement)
        result = {"status": "completed", "summary": "Legacy result", "files_changed": [], "commands": [],
                  "findings": [], "risks": []}
        connection.execute("UPDATE assignments SET result = ?, owner_pid = 42, owner_host = 'remote' WHERE id = 'A-0002'",
                           (json.dumps(result),))
        connection.execute("""INSERT INTO findings VALUES
            ('F-0001', 'T-0001', 'A-0002', 'major', 'Legacy finding', 'Keep evidence', '', 'open',
             NULL, '', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL)""")
        connection.commit()


def test_ledger_v3_migration_preserves_evidence_and_read_only_does_not_migrate(tmp_path: Path):
    db = tmp_path / "v3.sqlite"
    _v3_file(db)
    original = db.read_bytes()
    with closing(Ledger(db, read_only=True)) as store:
        before = _rows(store)
        findings = store.findings()
        assert all(a["owner_pid_namespace"] is None for a in store.show("T-0001")["assignments"])
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    assert db.read_bytes() == original
    with closing(Ledger(db)) as store:
        assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 6
        after = _rows(store)
        for table in ("tasks", "claims", "events"):
            assert after[table] == before[table]
        assert [row[:-5] for row in after["assignments"]] == before["assignments"]
        assert all(row[-5:-1] == (None,) * 4 for row in after['assignments'])
        assert [row[-1] for row in after['assignments']] == ['', ledger.LEGACY_ACK_NOTE, '']
        assert store.findings() == findings
        assert store.show("T-0001")["assignments"][1]["result"]["summary"] == "Legacy result"
        _error(lambda: store.connection.execute("DELETE FROM events"), "append-only", sqlite3.IntegrityError)
    with closing(Ledger(db)) as store:
        assert _rows(store) == after and store.findings() == findings


def test_ledger_v4_migration_failure_rolls_back_all_steps(tmp_path: Path):
    for version, seed in ((1, _v1_file), (3, _v3_file)):
        db = tmp_path / f"v{version}.sqlite"
        seed(db)
        with closing(sqlite3.connect(db)) as connection:
            before = _rows(SimpleNamespace(connection=connection))
        with patch.object(ledger, "MIGRATION_V3_TO_V4", (*ledger.MIGRATION_V3_TO_V4, "SELECT * FROM missing")):
            _error(lambda db=db: Ledger(db), "missing", sqlite3.OperationalError)
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute("SELECT version FROM schema_version").fetchone()[0] == version
            assert _rows(SimpleNamespace(connection=connection)) == before
            assert "owner_pid_namespace" not in [r[1] for r in connection.execute("PRAGMA table_info(assignments)")]
        with closing(Ledger(db)) as store:
            assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == 6


def test_ledger_never_probes_pid_or_group_across_namespaces(tmp_path: Path):
    # Host owner invisible in sandbox; sandbox PID reused by unrelated host process;
    # unreadable current namespace. None can justify reaping or assigning over the claim.
    for index, (owner_ns, local_ns, alive) in enumerate((
        ("pid:[host]", "pid:[sandbox]", False),
        ("pid:[sandbox]", "pid:[host]", True),
        ("pid:[sandbox]", None, False),
    )):
        probe = SimpleNamespace(calls=[])

        def pid_alive(pid, probe=probe, alive=alive):
            probe.calls.append(pid)
            return alive

        with closing(Ledger(tmp_path / f"ns-{index}.sqlite", hostname=lambda: "local",
                            boot_id=lambda: "boot-current", pid_namespace=lambda local_ns=local_ns: local_ns,
                            pid_alive=pid_alive)) as store:
            task = _open(store, tmp_path)["id"]
            holder = _assign(store, task, **OWNER, owner_pid_namespace=owner_ns)
            assert holder["owner_pid_namespace"] == owner_ns
            assert store.reap(dry_run=True) == [] and store.reap() == []
            _error(lambda task=task: _assign(store, task), holder["id"], ClaimConflictError)
            assert store.show(task)["assignments"] == [holder]
            assert store.show(task)["claims"] and probe.calls == []
            store.connection.execute("UPDATE assignments SET lease_until = '2000-01-01T00:00:00Z'")
            assert store.reap()[0]["reason"] == "lease_expired"
            assert probe.calls == []


def test_ledger_same_namespace_reaps_only_dead_owners_and_boot_change_still_recovers(tmp_path: Path):
    for alive in (False, True):
        with closing(_store(tmp_path / f"same-{alive}.sqlite", alive=alive, pid_namespace=lambda: "pid:[same]")) as store:
            task = _open(store, tmp_path)["id"]
            _assign(store, task, **OWNER, owner_pid_namespace="pid:[same]")
            result = store.reap()
            assert [item["reason"] for item in result] == ([] if alive else ["pid_gone"])
    with closing(_store(tmp_path / "reboot.sqlite", alive=True, pid_namespace=lambda: "pid:[new]")) as store:
        task = _open(store, tmp_path)["id"]
        _assign(store, task, **{**OWNER, "owner_boot_id": "boot-old"}, owner_pid_namespace="pid:[old]")
        assert store.reap()[0]["reason"] == "boot_id_changed"
    with patch.object(ledger.os, "readlink", side_effect=OSError("unavailable")):
        assert ledger._pid_namespace() is None


def test_ledger_pid_probe_requires_both_pid_and_group_gone():
    gone = ProcessLookupError(errno.ESRCH, "gone")
    for pid_outcome in (None, gone, PermissionError(errno.EPERM, "denied"), OSError(errno.EIO, "unknown")):
        for group_outcome in (None, gone, PermissionError(errno.EPERM, "denied"), OSError(errno.EIO, "unknown")):
            with (
                patch.object(ledger.sys, "platform", "linux"),
                patch.object(ledger.os, "kill", side_effect=pid_outcome) as kill,
                patch.object(ledger.os, "killpg", side_effect=group_outcome, create=True) as killpg,
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
            {"owner_pid_namespace": " "}, {"owner_pid_namespace": 123},
            {"lease_seconds": -1}, {"lease_seconds": True}, {"lease_seconds": 0.5}, {"lease_seconds": 2**63},
        ):
            before = _rows(store)
            _error(lambda arguments=arguments: _assign(store, task, **arguments), next(iter(arguments)))
            assert _rows(store) == before
        item = _assign(store, task, **OWNER, owner_pid_namespace="pid:[same]", lease_seconds=60)
        assert (datetime.fromisoformat(item["lease_until"]) - datetime.fromisoformat(item["started_at"])).total_seconds() == 60
        view = store.show(task)
        assert_strict_schema("task.schema.json", view)
        root = json.loads((SCHEMAS / "task.schema.json").read_text(encoding="utf-8"))
        schema = root["$defs"]["assignment"]
        for field, value in (
            ("owner_pid", True), ("owner_pid", "42"), ("owner_pid", -1), ("owner_pid", 2**31),
            ("owner_boot_id", 1), ("owner_host", []), ("lease_until", "not a timestamp"),
            ("owner_pid_namespace", []), ("owner_pid_namespace", ""),
        ):
            assert _strict_errors(schema, {**item, field: value}, root), (field, value)
        legacy = {key: value for key, value in item.items() if key not in (*OWNER, "lease_until", "owner_pid_namespace")}
        assert not _strict_errors(schema, legacy, root)  # Owner fields are optional in the artifact contract.


def test_ledger_cli_owner_fields_and_reap_dry_run_write_nothing(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(_store(db)) as store:
        task = _open(store, tmp_path)["id"]
    rc, stdout, stderr = run_cli_inprocess(
        "task", "assign", "--db", str(db), "--json", "--task", task, "--actor", "codex",
        "--role", "builder", "--mode", "rw", "--scope", "src", "--owner-pid", "42",
        "--owner-boot-id", "boot-old", "--owner-host", "remote", "--lease-seconds", "0",
        "--owner-pid-namespace", "pid:[foreign]",
    )
    assert rc == 0, stderr
    assigned = json.loads(stdout)
    assert assigned["owner_pid"] == 42 and assigned["owner_boot_id"] == "boot-old" and assigned["owner_host"] == "remote"
    assert assigned["owner_pid_namespace"] == "pid:[foreign]"
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
    if sys.platform == "win32":
        raise SkipTest("Windows chmod cannot enforce POSIX directory/sidecar permissions; ACL errors are mocked separately")
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


def test_ledger_read_only_wal_without_shm_reads_without_touching_store_or_wal(tmp_path: Path):
    """In a writable directory SQLite may add the -shm index; the store and WAL bytes stay untouched."""
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        task = _open(store, tmp_path)
        copy = tmp_path / "copy.sqlite"
        copy.write_bytes(db.read_bytes())
        Path(f"{copy}-wal").write_bytes(Path(f"{db}-wal").read_bytes())
        before = {name: (tmp_path / name).read_bytes() for name in ("copy.sqlite", "copy.sqlite-wal")}
        rc, stdout, _ = run_cli_inprocess("task", "board", "--db", str(copy), "--json")
        assert rc == 0 and task["id"] in stdout
        assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_ledger_read_only_wal_without_shm_in_readonly_directory_fails_without_creating_files(tmp_path: Path):
    connect = sqlite3.connect

    def enforce_directory_acl(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("?mode=ro&readonly_shm=1"):
            _deny_windows_shm_creation(copy)
        return connect(*args, **kwargs)

    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as store:
        _open(store, tmp_path)
        copy = tmp_path / "copy.sqlite"
        copy.write_bytes(db.read_bytes())
        Path(f"{copy}-wal").write_bytes(Path(f"{db}-wal").read_bytes())
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        with patch.object(ledger.os, "access", return_value=False), patch.object(
            ledger.sqlite3, "connect", side_effect=enforce_directory_acl
        ):
            rc, stdout, stderr = run_cli_inprocess("task", "board", "--db", str(copy), "--json")
            assert rc == 1 and "unable to open database file" in stderr
            _assert_error(_call(tmp_path, "task.board", ledger_db=copy), "unable to open database file")
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def _deny_windows_shm_creation(path):
    # Windows chmod cannot deny directory creation. SQLite's Win32 VFS uses OPEN_ALWAYS
    # even for readonly_shm=1 (unlike Unix O_RDONLY), so emulate the actual ACL denial,
    # not just os.access(). Keep native SQLite opens and the no-new-files assertions.
    if sys.platform == "win32" and not Path(f"{path}-shm").exists():
        failure = sqlite3.OperationalError("unable to open database file")
        failure.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
        raise failure


def test_ledger_read_only_wal_survives_last_writer_closing_during_open(tmp_path: Path):
    connect = sqlite3.connect

    def check(writable):
        parent = tmp_path / str(writable)
        db = parent / "ledger.sqlite"
        writer = Ledger(db)
        task = _open(writer, tmp_path)
        calls = []
        target = "?mode=ro" if writable else "?mode=ro&readonly_shm=1"

        def close_writer_before_connect(*args, suffix=target, **kwargs):
            if isinstance(args[0], str) and args[0].endswith(suffix):
                assert Path(f"{db}-wal").exists()
                calls.append(args[0])
                writer.close()
                assert not Path(f"{db}-wal").exists()
                if not writable:
                    parent.chmod(0o555)  # Enforce the sidecar restriction after cleanup too.
                    _deny_windows_shm_creation(db)
            return connect(*args, **kwargs)

        try:
            with patch.object(ledger.os, "access", return_value=writable), patch.object(
                ledger.sqlite3, "connect", side_effect=close_writer_before_connect
            ), closing(Ledger(db, read_only=True)) as reader:
                assert reader.show(task["id"]) == task
            assert len(calls) == 1, "The last-writer cleanup hook was not exercised"
            if not writable:
                assert list(parent.iterdir()) == [db]  # No sidecars recreated after cleanup.
        finally:
            parent.chmod(0o755)
            writer.close()

    for writable in (True, False):
        check(writable)


def test_ledger_read_only_native_sidecar_errors_fall_back_on_open_and_first_read(tmp_path: Path):
    connect = sqlite3.connect

    def check(db, task, live_wal, stage, code, message):
        calls, closed = [], []
        failure = sqlite3.OperationalError(message)
        failure.sqlite_errorcode = code

        class NativeConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql.startswith("SELECT"):
                    raise failure
                return super().execute(sql, *args)

            def close(self):
                closed.append(True)
                super().close()

        def fail_native(target, *args, **kwargs):
            calls.append(target)
            if isinstance(target, str) and target.endswith("?mode=ro"):
                if stage == "open":
                    raise failure
                kwargs["factory"] = NativeConnection
            return connect(target, *args, **kwargs)

        with patch.object(ledger.os, "access", return_value=True), patch.object(
            ledger.sqlite3, "connect", side_effect=fail_native
        ), closing(Ledger(db, read_only=True)) as reader:
            assert reader.show(task["id"]) == task
            _error(lambda: reader.connection.execute("DELETE FROM claims"), "readonly", sqlite3.OperationalError)
        assert closed == ([True] if stage == "read" else [])
        suffix = "&readonly_shm=1" if live_wal else "&immutable=1"
        assert any(target.endswith(suffix) for target in calls)
        assert all(target == ":memory:" or "?mode=ro" in target for target in calls)

    for live_wal in (False, True):
        db = tmp_path / f"fallback-{live_wal}.sqlite"
        writer = Ledger(db)
        task = _open(writer, tmp_path)
        if not live_wal:
            writer.close()
        try:
            for stage in ("open", "read"):
                for code, message in (
                    (sqlite3.SQLITE_PERM, "permission denied"),
                    (sqlite3.SQLITE_READONLY_CANTINIT, "attempt to write a readonly database"),
                    (sqlite3.SQLITE_CANTOPEN, "unable to open database file"),
                ):
                    check(db, task, live_wal, stage, code, message)
        finally:
            writer.close()


def test_ledger_read_only_native_directory_permission_mismatch_uses_snapshot(tmp_path: Path):
    if sys.platform == "win32":
        raise SkipTest("requires POSIX directory mode bits and geteuid; Windows ACL errors are mocked separately")
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as writer:
        task = _open(writer, tmp_path)
    original = db.read_bytes()
    tmp_path.chmod(0o555)
    try:
        # Emulate an ACL/capability probe that reports writable even though sidecar creation fails.
        with patch.object(ledger.os, "access", return_value=True), patch.object(
            ledger, "_sidecar_read_only_connection", wraps=ledger._sidecar_read_only_connection
        ) as fallback, closing(Ledger(db, read_only=True)) as reader:
            assert reader.show(task["id"]) == task
            if os.geteuid() != 0:  # Root may create sidecars despite the directory mode bits.
                assert fallback.call_count == 1
        assert db.read_bytes() == original
        if os.geteuid() != 0:
            assert list(tmp_path.iterdir()) == [db]
    finally:
        tmp_path.chmod(0o755)


def test_ledger_read_only_native_other_errors_do_not_fall_back(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as writer:
        _open(writer, tmp_path)
    for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_CORRUPT):
        failure = sqlite3.OperationalError("unrelated read failure")
        failure.sqlite_errorcode = code
        with patch.object(ledger.os, "access", return_value=True), patch.object(
            ledger.sqlite3, "connect", side_effect=failure
        ) as connect:
            _error(lambda: Ledger(db, read_only=True), str(failure), sqlite3.OperationalError)
            assert connect.call_count == 1


def test_ledger_read_only_fallback_refuses_hot_rollback_journal(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    _v1_file(db)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("CREATE TABLE padding (value BLOB)")
        connection.execute("INSERT INTO padding VALUES (zeroblob(1024000))")
        connection.commit()
    result = subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA cache_size = 1')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE padding SET value = zeroblob(2048000)')
os._exit(0)
""", str(db)], capture_output=True, text=True, timeout=15, check=True)
    assert result.returncode == 0
    journal = Path(f"{db}-journal")
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7"), "Journal must need recovery"
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    for writable in (True, False):
        with patch.object(ledger.os, "access", return_value=writable):
            _error(lambda: Ledger(db, read_only=True), "rollback journal")
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_ledger_read_only_retries_share_busy_timeout_budget(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    writer = Ledger(db)
    _open(writer, tmp_path)
    clock, attempts = [0.0], []
    budget = ledger.BUSY_TIMEOUT_MS / 1000
    failure = sqlite3.OperationalError("unable to open database file")
    failure.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN

    def connect(target, **kwargs):
        attempts.append((target, kwargs["timeout"]))
        if target.endswith("?mode=ro"):
            clock[0] += budget * 0.4
        else:
            assert target.endswith("&readonly_shm=1")
            writer.close()  # Last writer removes the WAL, so the next attempt would be a snapshot.
            clock[0] += kwargs["timeout"]
        raise failure

    def sleep(seconds):
        clock[0] += seconds

    try:
        with patch.object(ledger.os, "access", return_value=True), patch.object(
            ledger.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(ledger.time, "sleep", side_effect=sleep), patch.object(
            ledger.sqlite3, "connect", side_effect=connect
        ):
            _error(lambda: Ledger(db, read_only=True), "changed while opening")
        assert len(attempts) == 2
        assert attempts[0][1] == budget and attempts[1][1] == budget * 0.6
        assert clock[0] <= budget
    finally:
        writer.close()


def test_ledger_read_only_first_read_uses_remaining_open_budget(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as writer:
        _open(writer, tmp_path)
    connect = sqlite3.connect
    clock, timeouts, closed = [0.0], [], []
    budget = ledger.BUSY_TIMEOUT_MS / 1000
    failure = sqlite3.OperationalError("database is locked")
    failure.sqlite_errorcode = sqlite3.SQLITE_BUSY

    class SlowConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("SELECT"):
                timeout = super().execute("PRAGMA busy_timeout").fetchone()[0] / 1000
                timeouts.append(timeout)
                clock[0] += timeout
                raise failure
            return super().execute(sql, *args)

        def close(self):
            closed.append(True)
            super().close()

    def slow_open(*args, **kwargs):
        clock[0] += budget * 0.4
        return connect(*args, factory=SlowConnection, **kwargs)

    with patch.object(ledger.os, "access", return_value=True), patch.object(
        ledger.time, "monotonic", side_effect=lambda: clock[0]
    ), patch.object(ledger.sqlite3, "connect", side_effect=slow_open):
        _error(lambda: Ledger(db, read_only=True), str(failure), sqlite3.OperationalError)
    assert timeouts == [budget * 0.6] and closed == [True]
    assert clock[0] <= budget


def test_ledger_read_only_snapshot_copy_obeys_deadline_and_closes_on_timeout(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as writer:
        _open(writer, tmp_path)
    connect = sqlite3.connect
    clock, snapshots, steps, closed = [0.0], [], [], []
    budget = ledger.BUSY_TIMEOUT_MS / 1000

    def slow_copy(snapshot, *, progress, **options):
        snapshots.append(snapshot)
        while True:
            clock[0] += budget / 4
            steps.append(clock[0])
            progress(sqlite3.SQLITE_BUSY, 1, 2)

    def open_source(target, **kwargs):
        connection = connect(target, **kwargs)
        if target.endswith("&immutable=1"):
            def close():
                closed.append(True)
                connection.close()
            return SimpleNamespace(backup=slow_copy, close=close)
        return connection

    with patch.object(ledger.os, "access", return_value=False), patch.object(
        ledger.time, "monotonic", side_effect=lambda: clock[0]
    ), patch.object(ledger.sqlite3, "connect", side_effect=open_source):
        _error(lambda: Ledger(db, read_only=True), "changed while opening")
    assert len(steps) == 4 and clock[0] == budget and closed == [True]
    assert len(snapshots) == 1
    _error(lambda: snapshots[0].execute("SELECT 1"), "closed database", sqlite3.ProgrammingError)


def test_ledger_read_only_wal_keeps_same_process_writer_lock(tmp_path: Path):
    db = tmp_path / "ledger.sqlite"
    with closing(Ledger(db)) as writer:
        task = _open(writer, tmp_path)
        writer.connection.execute("BEGIN IMMEDIATE")
        writer.connection.execute("UPDATE tasks SET title = 'Uncommitted'")
        with patch.object(ledger.os, "access", return_value=False), closing(Ledger(db, read_only=True)) as reader:
            assert reader.show(task["id"])["title"] == task["title"]
            # An unrelated descriptor close must not release this process's SQLite writer locks.
            result = subprocess.run([sys.executable, "-c", """
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=0)
try:
    connection.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as exc:
    assert exc.sqlite_errorcode == sqlite3.SQLITE_BUSY
else:
    raise AssertionError("Reader released another connection's writer lock")
""", str(db)], capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
        writer.connection.rollback()


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
                def backup(snapshot, **options):
                    nonlocal writer, written
                    source.backup(snapshot, **options)
                    writer = Ledger(active_db)
                    written = _open(writer, tmp_path, title="Concurrent commit")
                    if not retain_wal:
                        writer.close()
                        writer = None
                return SimpleNamespace(backup=backup, close=source.close)
            return source

        try:
            # Force the snapshot path, independent of the test user's directory permissions.
            with patch.object(ledger.os, "access", return_value=False), patch.object(
                ledger.sqlite3, "connect", side_effect=connect_and_write
            ), closing(Ledger(db, read_only=True)) as reader:
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
    arguments = {**WRITE_ARGUMENTS["task.assign"], "task": task, **OWNER, "owner_pid_namespace": "pid:[foreign]",
                 "lease_seconds": 0, "approved": True}
    assigned = _payload(_call(tmp_path, "task.assign", ledger_db=db, **arguments))
    _assert_assignment(assigned)
    assert all(assigned[key] == value for key, value in OWNER.items())
    assert assigned["owner_pid_namespace"] == "pid:[foreign]"
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
        ("owner_pid_namespace", None), ("owner_pid_namespace", ""), ("owner_pid_namespace", 1),
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
    if sys.platform != "linux":
        raise SkipTest("requires Linux prctl child subreaping, /proc boot identity and SIGKILL process groups")
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
