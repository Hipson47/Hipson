"""Windows worker containment. All handles stay private to the runner process."""

from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IOCounters), ("process_memory", ctypes.c_size_t),
               ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
               ("peak_job_memory", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("user_time", "kernel_time", "period_user", "period_kernel")] + [
        (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("tid", wintypes.DWORD),
               ("pid", wintypes.DWORD), ("priority", wintypes.LONG), ("delta", wintypes.LONG),
               ("flags", wintypes.DWORD)]


class Job:
    """Kill the entire worker tree on close, including when the runner is hard-killed."""

    def __init__(self) -> None:
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        # Explicit signatures are necessary on 64-bit Windows: HANDLE is pointer-sized.
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "GenerateConsoleCtrlEvent": ([wintypes.DWORD, wintypes.DWORD], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.kernel.CreateJobObjectW(None, None)  # Non-inheritable, unnamed job.
        if not self.handle:
            self._error()
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError()  # type: ignore[attr-defined]
            self.close()
            raise error

    @staticmethod
    def _error() -> None:
        raise ctypes.WinError()  # type: ignore[attr-defined]

    def attach(self, child: subprocess.Popen[bytes]) -> None:
        # The process was created suspended; it cannot execute/spawn before assignment.
        handle = self.kernel.OpenProcess(0x0101, False, child.pid)  # SET_QUOTA | TERMINATE
        if not handle:
            self._error()
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, handle):
                self._error()
        finally:
            self.kernel.CloseHandle(handle)
        # Popen closes CreateProcess's thread handle. Recover that initial thread using
        # documented Toolhelp APIs, rather than depending on undocumented NtResumeProcess.
        snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            self._error()
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = self.kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid == child.pid:
                    thread = self.kernel.OpenThread(2, False, entry.tid)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        self._error()
                    try:
                        if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            self._error()
                    finally:
                        self.kernel.CloseHandle(thread)
                    return
                found = self.kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Suspended worker thread was not found")
        finally:
            self.kernel.CloseHandle(snapshot)

    def active(self) -> bool:
        info = _Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            self._error()
        return bool(info.active)

    def interrupt(self, group: int) -> None:
        # A group can outlive its leader. Popen.send_signal would skip an exited
        # direct child, so address the surviving console group explicitly. The
        # caller must share its console; CREATE_NEW_PROCESS_GROUP only masks Ctrl+C,
        # not Ctrl+Break. Job assignment and CREATE_SUSPENDED do not change that.
        if not self.kernel.GenerateConsoleCtrlEvent(1, group):  # CTRL_BREAK_EVENT
            self._error()

    def terminate(self) -> None:
        if not self.kernel.TerminateJobObject(self.handle, 1):
            self._error()

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
