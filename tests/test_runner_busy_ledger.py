"""A busy ledger (another process holding the write lock past SQLite's busy timeout) must not cost a worker.

Reproduced on 1.8.1 before the fix: a 25 s worker was stopped with SIGTERM and recorded `failed`
(`run.launch_failed: database is locked`) while another `task open` fingerprinted a slow /mnt/c project for 12 s.
The worker and the lock holder synchronise on sentinel files, so the lock always covers the step under test.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

from hipson import ledger
from tests.test_task_verification import _project

RESULT = {"status": "completed", "summary": "slow worker finished", "files_changed": [], "commands": [],
          "findings": [], "risks": []}
WORKER = """
import json, os, sys, time
result, started, locked, after = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
open(started, "w").close()
deadline = time.monotonic() + 60
while not os.path.exists(locked):
    if time.monotonic() > deadline:
        sys.exit("the lock holder never took the lock")
    time.sleep(0.02)
time.sleep(after)
json.dump(RESULT, open(result, "w"))
""".replace("RESULT", repr(RESULT))


def _hold_write_lock(db: Path, started: Path, locked: Path, seconds: float, errors: list[str]) -> None:
    """Take SQLite's write lock once the worker runs, like a lead fingerprinting a slow project, then release it."""
    deadline = time.monotonic() + 60
    while not started.exists():
        if time.monotonic() > deadline:
            errors.append("the worker never started")
            return
        time.sleep(0.02)
    with closing(sqlite3.connect(db, timeout=60, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        locked.touch()
        time.sleep(seconds)
        connection.execute("COMMIT")


def _run_worker(tmp_path: Path, *, after_lock: float, lock_seconds: float):
    project = _project(tmp_path)
    db = tmp_path / "ledger.sqlite"
    result_file, started, locked = tmp_path / "result.json", tmp_path / "started", tmp_path / "locked"
    with closing(ledger.Ledger(db)) as store:
        task = store.open_task(project=project, title="busy", objective="survive a busy ledger")["id"]
        store.set_status(task, actor="lead", to="in_progress")
    errors: list[str] = []
    holder = threading.Thread(target=_hold_write_lock, args=(db, started, locked, lock_seconds, errors))
    # A short busy timeout makes the lock outlast it without a slow test; the shape of the failure is the same.
    # 60 attempts of 0.2 s: the final record outlasts any lock the test holds, however slow the CI machine.
    runner = ("import sys\nfrom hipson import cli, ledger, task_runner\nledger.BUSY_TIMEOUT_MS = 200\n"
              "task_runner.FINISH_BUSY_ATTEMPTS = 60\nraise SystemExit(cli.main(sys.argv[1:]))\n")
    argv = [sys.executable, "-c", runner, "task", "run", "--db", str(db), "--task", task, "--actor", "w",
            "--role", "builder", "--mode", "rw", "--scope", ".", "--result-file", str(result_file), "--",
            sys.executable, "-c", WORKER, str(result_file), str(started), str(locked), str(after_lock)]
    holder.start()
    try:
        proc = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=180,
                              env={key: value for key, value in os.environ.items() if not key.startswith("HIPSON_")})
    finally:
        holder.join()
    assert not errors and locked.exists(), (errors, proc.stderr)
    with closing(ledger.Ledger(db, read_only=True)) as store:
        assignment = store.show(task)["assignments"][0]
    return proc.returncode, assignment, proc.stderr, result_file


def test_a_busy_ledger_during_heartbeats_never_stops_the_worker(tmp_path):
    # The lock is held for 2.5 s while the worker still runs: at least one 1 s heartbeat meets it.
    code, assignment, stderr, result_file = _run_worker(tmp_path, after_lock=2.0, lock_seconds=2.5)
    assert code == 0, stderr
    assert "skipped an activity heartbeat" in stderr and "launch_failed" not in stderr, stderr
    assert json.loads(result_file.read_text()) == RESULT  # The worker ran to its end.
    assert assignment["status"] == "passed" and assignment["result"]["summary"] == "slow worker finished"


def test_the_final_record_waits_out_a_busy_ledger_instead_of_leaving_the_assignment_active(tmp_path):
    # The worker ends as soon as the lock is taken and the lock stays 3 s, longer than the supervisor needs to reach
    # its final record even on a slow macOS runner: the first attempts fail busy and a retry records it.
    code, assignment, stderr, _ = _run_worker(tmp_path, after_lock=0.0, lock_seconds=3.0)
    assert code == 0, stderr
    assert "retrying the final record" in stderr and "could not finish" not in stderr, stderr
    assert assignment["status"] == "passed" and assignment["result"]["status"] == "completed", stderr
