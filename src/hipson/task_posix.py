"""POSIX descendant tracking; Linux subreaping also covers double-forked workers."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys


class ProcessTree:
    def __init__(self) -> None:
        self.owner = os.getpid()
        self.known: dict[int, str] = {}
        self.libc = None
        self.previous = ctypes.c_int()
        self.child: subprocess.Popen[bytes] | None = None
        if sys.platform == "linux":
            self.libc = ctypes.CDLL(None, use_errno=True)
            # Fail closed before assignment if reliable orphan adoption is unavailable.
            if self.libc.prctl(37, ctypes.byref(self.previous), 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER failed")
            if self.libc.prctl(36, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")

    def members(self) -> set[int]:
        # lstart prevents following a recycled PID in the macOS best-effort tracker.
        # Exclude our own ps probe, which is also a (short-lived) descendant.
        with subprocess.Popen(["ps", "-e", "-o", "pid=,ppid=,pgid=,stat=,lstart="],
                              stdout=subprocess.PIPE, text=True, encoding="ascii", errors="strict",
                              env={**os.environ, "LC_ALL": "C"}, start_new_session=True) as probe:
            try:
                output, _ = probe.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                probe.kill()
                probe.communicate()
                raise
            if probe.returncode:
                raise OSError("could not inspect worker process tree")
        rows = {}
        for line in output.splitlines():
            raw_pid, parent, group, state, started = line.split(maxsplit=4)
            if int(raw_pid) not in (self.owner, probe.pid):
                rows[int(raw_pid)] = (int(parent), int(group), state, started)
        tracked = {pid for pid, row in rows.items()
                   if row[1] == self.owner or self.known.get(pid) == row[3]}
        parents = {self.owner, *tracked}
        while descendants := {pid for pid, row in rows.items() if row[0] in parents} - tracked:
            tracked.update(descendants)
            parents.update(descendants)
        self.known = {pid: rows[pid][3] for pid in tracked}
        live = set()
        for pid in tracked:
            if not rows[pid][2].startswith("Z"):
                live.add(pid)
            elif rows[pid][0] == self.owner and sys.platform != "win32":
                # Only reap known adopted zombies. Popen owns its direct child's status.
                if self.child is None or pid != self.child.pid:
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
        return live

    def active(self) -> bool:
        if self.members():
            return True
        if sys.platform == "linux":
            # ps is not an atomic snapshot: a parent can disappear before its
            # newly adopted child is observed. Only ECHILD proves the subreaper
            # has no remaining descendants. Peek without stealing Popen's status.
            for _ in range(1024):
                try:
                    status = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    if status is None:
                        return True  # Children exist, but none has exited.
                    if self.child is not None and status.si_pid == self.child.pid:
                        self.child.poll()
                    else:
                        os.waitpid(status.si_pid, os.WNOHANG)
                except ChildProcessError:
                    return False
            return True  # Bound reaping work; retain claims if exits keep arriving.
        return False

    def send(self, number: int) -> None:
        if sys.platform == "win32":
            raise OSError("POSIX process tracking is unavailable on Windows")
        for pid in self.members():
            try:
                os.kill(pid, number)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if self.libc is not None:
            if self.libc.prctl(36, self.previous.value, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "could not restore child subreaper setting")


def worker_environment(owner: int, db: str, assignment: str) -> set[bytes]:
    """Identity markers for best-effort recovery, without reading/logging other values."""
    return {os.fsencode(f"HIPSON_RUN_OWNER={owner}"), os.fsencode(f"HIPSON_RUN_DB={db}"),
            os.fsencode(f"HIPSON_ASSIGNMENT={assignment}")}


def marked_descendant_alive(owner: int, db: str, assignment: str) -> bool:
    """Linux recovery of detached workers that retain the runner's inherited markers."""
    if sys.platform != "linux":
        return False
    from pathlib import Path

    expected = worker_environment(owner, db, assignment)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # Bound reads even if a worker has an unusually large environment.
            with (entry / "environ").open("rb") as stream:
                values = stream.read(2**20).split(b"\0")
            if expected.issubset(values):
                return True
        except (OSError, ValueError):
            continue  # Unreadable/unmarked processes cannot be attributed to this owner.
    return False
