"""A busy ledger (another process holding the write lock past SQLite's busy timeout) must not cost a worker.

Reproduced on 1.8.1 before the fix: a 25 s worker was stopped with SIGTERM and recorded `failed`
(`run.launch_failed: database is locked`) while another `task open` fingerprinted a slow /mnt/c project for 12 s.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing

from hipson import ledger
from tests.test_task_verification import _project

RESULT = {"status": "completed", "summary": "slow worker finished", "files_changed": [], "commands": [],
          "findings": [], "risks": []}


def _hold_write_lock(db, start, seconds, held):
    """Hold SQLite's write lock from another connection, like a lead fingerprinting a slow project."""
    time.sleep(start)
    with closing(sqlite3.connect(db, timeout=30, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        held.set()
        time.sleep(seconds)
        connection.execute("COMMIT")


def _run_worker(tmp_path, *, sleep, lock_start, lock_seconds):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"
    result_file = tmp_path / "result.json"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="busy", objective="survive a busy ledger")["id"]
        store.set_status(task, actor="lead", to="in_progress")
    worker = (f"import json, sys, time; time.sleep({sleep}); "
              f"json.dump({RESULT!r}, open(sys.argv[1], 'w'))")
    held = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(db, lock_start, lock_seconds, held))
    # A short busy timeout makes the lock outlast it without a slow test; the shape of the failure is the same.
    runner = ("import sys\nfrom hipson import cli, ledger\nledger.BUSY_TIMEOUT_MS = 200\n"
              "raise SystemExit(cli.main(sys.argv[1:]))\n")
    argv = [sys.executable, "-c", runner, "task", "run", "--db", str(db), "--task", task, "--actor", "w",
            "--role", "builder", "--mode", "rw", "--scope", ".", "--result-file", str(result_file), "--",
            sys.executable, "-c", worker, str(result_file)]
    holder.start()
    try:
        proc = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=120,
                              env={key: value for key, value in os.environ.items() if not key.startswith("HIPSON_")})
    finally:
        holder.join()
    assert held.is_set()
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assignment = store.show(task)["assignments"][0]
    return proc.returncode, assignment, proc.stderr, result_file


def test_a_busy_ledger_during_heartbeats_never_stops_the_worker(tmp_path):
    code, assignment, stderr, result_file = _run_worker(tmp_path, sleep=2.5, lock_start=0.8, lock_seconds=1.2)
    assert code == 0, stderr
    assert "skipped an activity heartbeat" in stderr and "launch_failed" not in stderr, stderr
    assert json.loads(result_file.read_text()) == RESULT  # The worker ran to its end.
    assert assignment["status"] == "passed" and assignment["result"]["summary"] == "slow worker finished"


def test_the_final_record_waits_out_a_busy_ledger_instead_of_leaving_the_assignment_active(tmp_path):
    code, assignment, stderr, _ = _run_worker(tmp_path, sleep=0.3, lock_start=0.25, lock_seconds=0.9)
    assert code == 0, stderr
    assert assignment["status"] == "passed" and assignment["result"]["status"] == "completed", stderr
    assert "could not finish" not in stderr, stderr
