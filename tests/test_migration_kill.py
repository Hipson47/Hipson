"""A writer killed for real (SIGKILL / TerminateProcess) while it backs up or migrates an older ledger.

Before 1.8.1 a kill during the pre-migration copy left an empty or hot-journal file under the final backup name
(`<ledger>.v11-backup-<ts>`): 40 of 40 kills on a 37 MB ledger reproduced it, and the rollback steps would have
restored it. The copy is now written to a hidden `.partial` file and named only when complete.
"""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from hipson import ledger

CHILD = r"""
import sqlite3, sys
from hipson import ledger

db, point = sys.argv[1], sys.argv[2]


class Blocking(sqlite3.Connection):
    def backup(self, target, *args, **kwargs):
        def progress(status, remaining, total):
            if point == "backup":
                print("READY", flush=True)
                sys.stdin.readline()  # Killed here, after the first page of the copy.
        return super().backup(target, pages=1, progress=progress)


real_connect = sqlite3.connect


def tracing_connect(*args, **kwargs):
    if not str(args[0]).endswith(db) and db not in str(args[0]):
        return real_connect(*args, **kwargs)
    connection = real_connect(*args, factory=Blocking, **kwargs)
    if point == "migration":
        def trace(statement):
            if "ADD COLUMN usage" in statement:
                print("READY", flush=True)
                sys.stdin.readline()  # Killed here: the backup is complete, the migration not yet run.
        connection.set_trace_callback(trace)
    return connection


ledger.sqlite3.connect = tracing_connect
ledger.Ledger(db).close()
print("NOT KILLED", flush=True)
"""


def _old_ledger(path: Path) -> None:
    with closing(ledger.Ledger(path)) as store:
        task = store.open_task(project=path.parent, title="kept", objective="x" * 200_000)["id"]
        for index in range(300):  # Enough pages that the copy takes many backup steps.
            store.add_event(task, actor="lead", type="note", summary=f"event {index} " + "y" * 500)
        store.connection.execute("ALTER TABLE assignments DROP COLUMN usage")
        store.connection.execute("UPDATE schema_version SET version = 11")


def _state(path: Path) -> tuple[str, int, list[str]]:
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        return (connection.execute("PRAGMA integrity_check").fetchone()[0],
                connection.execute("SELECT version FROM schema_version").fetchone()[0],
                [row[0] for row in connection.execute("SELECT title FROM tasks")])


def _backups(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("ledger.sqlite.v*-backup-*")
                  if not path.name.endswith(("-journal", "-wal", "-shm")))


def test_a_real_kill_during_backup_or_migration_never_leaves_a_fake_backup_or_a_broken_ledger(tmp_path):
    for point in ("backup", "migration"):
        directory = tmp_path / point
        directory.mkdir()
        db = directory / "ledger.sqlite"
        _old_ledger(db)
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(db), point], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = child.stdout.readline().strip() if child.stdout is not None else ""
            assert line == "READY", (point, line, child.stderr.read() if line == "" and child.stderr else "")
        finally:
            child.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no cleanup code runs.
            child.communicate(timeout=30)
        assert _state(db) == ("ok", 11, ["kept"]), point  # Untouched: still the old schema, rows intact.
        backups = _backups(directory)
        if point == "backup":
            assert backups == [], (point, [path.name for path in directory.iterdir()])
        else:
            assert len(backups) == 1, (point, [path.name for path in directory.iterdir()])
            assert _state(backups[0]) == ("ok", 11, ["kept"]), point
        # The next writer recovers: it migrates once and leaves exactly one valid old-schema backup more.
        before = set(backups)
        with closing(ledger.Ledger(db)) as store:
            assert store.connection.execute("SELECT version FROM schema_version").fetchone()[0] == ledger.SCHEMA_VERSION
        new = [path for path in _backups(directory) if path not in before]
        assert len(new) == 1 and _state(new[0]) == ("ok", 11, ["kept"]), point
        assert _state(db)[0] == "ok", point
