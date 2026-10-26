"""Init client/MCP cleanup within a POSIX process group or a Windows Job.

POSIX descendants that create a new session/process group escape this containment.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from hipson.task_windows import Job


def run_contained(
    command: str | list[str], project: Path, *, env: dict[str, str] | None,
    input_text: str | None, timeout: float,
) -> subprocess.CompletedProcess[str]:
    windows = sys.platform == "win32"
    job = Job() if windows else None
    child: subprocess.Popen[bytes] | None = None
    previous = None

    def interrupted(number: int, frame: object) -> None:
        raise InterruptedError(f"Init subprocess interrupted by signal {number}")

    try:
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, interrupted)
        if job:
            job.protect_runner()
        child = subprocess.Popen(
            command, cwd=project, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=not windows,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | 4) if windows else 0,  # type: ignore[attr-defined]  # CREATE_SUSPENDED
        )
        if job:
            job.attach(child)
        stdout, stderr = child.communicate(None if input_text is None else input_text.encode("utf-8"), timeout=timeout)
        return subprocess.CompletedProcess(command, child.returncode, stdout.decode("utf-8"), stderr.decode("utf-8"))
    finally:
        # Also stop descendants still in the group/Job after a successful launcher.
        try:
            if job:
                job.close()  # KILL_ON_JOB_CLOSE also covers .cmd grandchildren.
            elif child and sys.platform != "win32":
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            try:
                if child:
                    if child.poll() is None:
                        child.kill()  # Also handles a Windows attach failure before assignment.
                    child.communicate(timeout=5)
            finally:
                if previous is not None:
                    signal.signal(signal.SIGTERM, previous)
