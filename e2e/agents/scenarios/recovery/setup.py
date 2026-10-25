"""Leave a real exited/reaped owner with an active ledger claim (no raw DB edits)."""

import socket
import subprocess
import sys
from pathlib import Path


def setup(fixture):
    task = fixture.open_task("Recover crashed parser worker")
    owner = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    owner.wait(timeout=5)
    assignment = fixture.cli("task", "assign", "--task", task, "--actor", "crashed-builder", "--role", "builder",
                             "--mode", "rw", "--scope", "src/parser", "--owner-pid", str(owner.pid),
                             "--owner-host", socket.gethostname(), "--owner-boot-id",
                             Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    return {"task": task, "stale": assignment["id"]}
