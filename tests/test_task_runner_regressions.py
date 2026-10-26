"""Regression evidence for worker containment and rejected result files."""

import argparse
import os
import signal
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from hipson import ledger, task_runner
from tests import test_agent_run as bash
from tests.test_task_runner import _case, _ready, _stopped


def _rejected_result(tmp_path, content, code):
    with _case(tmp_path) as run:
        result = tmp_path / "result.json"
        result.write_bytes(content)
        run.env["PYTHONINTMAXSTRDIGITS"] = "4300"
        process = run.start(f"raise SystemExit({code})", extra=("--result-file", str(result)))
        assert run.wait(process) == code, run.stderr(process)
        view, events = run.view()
        assert view["assignments"][0]["status"] == ("passed" if code == 0 else "failed")
        assert view["claims"] == [] and view["assignments"][0]["result"] is None
        assert "result file rejected" in events[-1]["summary"]
        assert f"worker process exit={code}" in events[-1]["summary"]


def test_task_run_result_integer_parser_limit_falls_back(tmp_path):
    _rejected_result(tmp_path, b'{"summary":' + b"9" * 5000 + b"}", 7)


def test_task_run_result_recursion_limit_falls_back(tmp_path):
    _rejected_result(tmp_path, b"[" * 10000 + b"0" + b"]" * 10000, 0)


def test_task_run_result_loader_exceptions_cannot_override_process_outcome(tmp_path):
    result = tmp_path / "result.json"
    result.write_text("{}", encoding="utf-8")
    args = argparse.Namespace(ref=[], rollout=None, result_file=str(result))
    for error in (MemoryError(), ValueError("limit"), RecursionError(), RuntimeError("validator"),
                  UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")):
        store = Mock()
        with patch.object(ledger, "load_task_result", side_effect=error), patch.object(ledger, "Ledger", return_value=store):
            task_runner._finish(tmp_path / "unused.db", args, "A-0001", 7, 0, "", "nonce", time.time(), {}, str(tmp_path))
        store.finish.assert_called_once()
        assert store.finish.call_args.kwargs["result"] == "failed"
        assert store.finish.call_args.kwargs["worker_result"] is None
        assert store.finish.call_args.kwargs['failure_journal']['result_state'] == 'rejected'
        assert store.finish.call_args.kwargs['failure_journal']['exit_code'] == 7


def test_task_run_result_file_read_is_bounded(tmp_path):
    path = tmp_path / "huge.json"
    with path.open("wb") as stream:
        stream.truncate(2**20 + 1)
    with patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")):
        try:
            ledger.load_task_result(path)
        except ledger.LedgerError as exc:
            assert "limit" in str(exc)
        else:
            raise AssertionError("Oversized evidence must be rejected before parsing")


def _caller_sibling(tmp_path, cancel):
    if sys.platform == "win32":
        return  # Windows isolation is exercised through its Job regression below.
    with _case(tmp_path) as run:
        bootstrap = """
import time
from pathlib import Path
Path('bootstrap-ready').touch()
while not Path('sibling-ready').exists(): time.sleep(0.01)
"""
        process = run.start("""
import json, os, time
from pathlib import Path
Path('ready.tmp').write_text(json.dumps([os.getpid()]))
Path('ready.tmp').rename('ready.json')
while not Path('exit-worker').exists(): time.sleep(0.01)
""", group_leader=True, before_main=bootstrap)
        bash._wait_for(lambda: (tmp_path / "bootstrap-ready").exists(), "Caller did not enter its group")
        # Same parent and caller session, with the sibling present before main()
        # assigns the worker. This models another stage of a shell pipeline.
        sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                   process_group=process.pid)
        run.processes.append(sibling)
        (tmp_path / "sibling-ready").touch()
        _ready(run, process)
        owner = run.view()[0]["assignments"][0]["owner_pid"]
        assert os.getpgid(sibling.pid) == process.pid
        if cancel:
            process.terminate()
        else:
            (tmp_path / "exit-worker").touch()
        assert run.wait(process) == (143 if cancel else 0), run.stderr(process)
        assert sibling.poll() is None, "Runner cleanup killed an unrelated caller-group sibling"
        assert owner != process.pid, "A group-leading caller needs a private supervisor owner"
        assert run.view()[0]["claims"] == []


def test_task_run_group_leading_caller_sibling_survives_cleanup(tmp_path):
    _caller_sibling(tmp_path, False)


def test_task_run_group_leading_proxy_forwards_signals(tmp_path):
    _caller_sibling(tmp_path, True)


_DETACHED_WORKER = """
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', '''
import signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('detached-ready').touch()
time.sleep(120)
'''], start_new_session=True)
while not Path('detached-ready').exists(): time.sleep(0.01)
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
while not Path('exit-worker').exists(): time.sleep(0.01)
"""


def test_task_run_detached_descendant_stops_before_claim_release(tmp_path):
    if sys.platform == "win32":
        return  # Job membership already covers descendants with new process groups.
    with _case(tmp_path) as run:
        process = run.start(_DETACHED_WORKER)
        pids = _ready(run, process)
        assert os.getpgid(pids[1]) != process.pid
        # Give the macOS process-tree tracker an opportunity to observe the child.
        time.sleep(0.2)
        (tmp_path / "exit-worker").touch()
        def finished():
            endings = [e for e in run.view()[1] if e["type"] == "finished"]
            if endings:
                assert _stopped(pids), "Claims released while a detached descendant could write"
                return endings[-1]
            return None
        ending = bash._wait_for(finished, "Runner did not clean up detached descendant")
        assert run.wait(process) == 0, run.stderr(process)
        assert "leftover processes stopped" in ending["summary"]
        assert run.view()[0]["claims"] == []


def test_task_run_detached_descendant_retains_claim_after_owner_kill(tmp_path):
    if sys.platform != "linux":
        return  # Recovery uses Linux /proc environment identity, with limits documented.
    with _case(tmp_path) as run:
        process = run.start(_DETACHED_WORKER)
        worker, detached = _ready(run, process)
        process.kill()
        assert run.wait(process) == -signal.SIGKILL
        os.kill(worker, signal.SIGKILL)
        bash._wait_for(lambda: _stopped([worker]), "Original group did not stop")
        with closing(ledger.Ledger(run.db)) as store:
            assert store.reap() == [], "Reap released a claim with an identifiable detached descendant"
            assert len(store.show(run.task)["claims"]) == 1
            os.kill(detached, signal.SIGKILL)
            bash._wait_for(lambda: _stopped([detached]), "Detached child did not stop")
            reaped = store.reap()[0]
            assert reaped['reason'] == 'pid_gone'
            journal = reaped['assignment']['failure_journal']
            assert journal['exit_code'] is journal['signal'] is None
            assert 'owner died; last activity at ' in journal['reason']


def test_task_run_linux_empty_snapshot_cannot_release_live_children():
    if sys.platform != "linux":
        return
    from hipson.task_posix import ProcessTree

    tree = ProcessTree()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
    tree.child = child
    try:
        with patch.object(tree, "members", return_value=set()):
            assert tree.active(), "An empty ps snapshot cannot prove a Linux descendant gone"
    finally:
        child.kill()
        child.wait(timeout=5)
        tree.close()


def test_task_run_windows_contains_runner_before_process_creation(tmp_path):
    # Execute the Windows control flow on every CI host, then the actual startup
    # kill on Windows. The gate pauses AFTER CreateProcess but BEFORE attach/resume.
    calls = []
    job = Mock()
    job.protect_runner.side_effect = lambda: calls.append("protect")
    child = Mock(pid=42)
    child.poll.return_value = 0
    child.wait.return_value = 0
    def create(*args, **kwargs):
        calls.append("create")
        return child
    store = Mock()
    store.assign.return_value = {"id": "A-0001"}
    args = argparse.Namespace(task="T-0001", actor="test", role="builder", mode="rw", scope=["src"],
                              lease_seconds=None, rollout=None)
    with patch.object(task_runner.sys, "platform", "win32"), patch.object(signal, "SIGBREAK", 21, create=True), \
            patch.object(signal, "signal"), patch.object(task_runner, "Job", return_value=job), \
            patch.object(ledger, "Ledger", return_value=store), patch.object(task_runner, "_active", return_value=False), \
            patch.object(task_runner, "_finish"), patch.object(subprocess, "Popen", side_effect=create), \
            patch.object(task_runner.task_activity, 'Activity') as activity, \
            patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
        activity.return_value.snapshot.return_value = {}
        assert task_runner._execute(tmp_path / "unused.db", args, ["worker"], False, {'expected': str(tmp_path)}, 0) == 0
    assert calls == ["protect", "create"], "Worker creation must inherit pre-established containment"


def test_task_run_windows_startup_kill_leaves_no_suspended_orphan(tmp_path):
    if sys.platform != "win32":
        return
    with _case(tmp_path) as run:
        bootstrap = """
import subprocess, time
from pathlib import Path
original_popen = subprocess.Popen
def startup_gate(*args, **kwargs):
    child = original_popen(*args, **kwargs)
    if kwargs.get('creationflags', 0) & 4:  # CREATE_SUSPENDED: the worker, never the git probe.
        Path('startup.tmp').write_text(str(child.pid))
        Path('startup.tmp').rename('startup.pid')
        while True: time.sleep(0.01)
    return child
subprocess.Popen = startup_gate
"""
        process = run.start("from pathlib import Path; Path('executed').touch()", before_main=bootstrap)
        bash._wait_for(lambda: (tmp_path / "startup.pid").exists(), "Runner missed startup gate")
        pid = int((tmp_path / "startup.pid").read_text())
        run.workers.append(pid)
        run.kill(process)
        run.wait(process)
        bash._wait_for(lambda: not ledger._pid_alive(pid), "Startup kill left an unassigned suspended orphan")
        assert not (tmp_path / "executed").exists()
        with closing(ledger.Ledger(run.db)) as store:
            assert store.reap()[0]["reason"] == "pid_gone"


def test_task_run_windows_guardian_handle_lifetime_and_fail_closed():
    import ctypes
    from types import SimpleNamespace

    from hipson import task_windows

    kernel = SimpleNamespace(**{name: Mock(return_value=True) for name in (
        "CreateJobObjectW", "SetInformationJobObject", "QueryInformationJobObject", "AssignProcessToJobObject",
        "GetCurrentProcess", "GenerateConsoleCtrlEvent", "TerminateJobObject", "OpenProcess", "CloseHandle",
        "CreateToolhelp32Snapshot", "Thread32First", "Thread32Next", "OpenThread", "ResumeThread")})
    kernel.CreateJobObjectW.side_effect = [101, 102, 103]
    kernel.GetCurrentProcess.return_value = -1
    with patch.object(ctypes, "WinDLL", return_value=kernel, create=True), patch.object(task_windows, "_guardian", None):
        job = task_windows.Job()
        job.protect_runner()
        kernel.AssignProcessToJobObject.assert_called_once_with(102, -1)
        # There is exactly one guardian per interpreter, whose handle must remain
        # open after ordinary worker cleanup or the runner itself would be killed.
        job.protect_runner()
        assert kernel.CreateJobObjectW.call_count == 2
        job.close()
        kernel.CloseHandle.assert_called_once_with(101)
        assert task_windows._guardian.handle == 102
        with patch.object(task_windows, "_guardian", None), \
                patch.object(ctypes, "WinError", return_value=OSError("guardian denied"), create=True):
            kernel.AssignProcessToJobObject.return_value = False
            try:
                job.protect_runner()
            except OSError as exc:
                assert "guardian denied" in str(exc)
            else:
                raise AssertionError("Failed inherited containment must prevent launch")
            assert task_windows._guardian is None
            kernel.CloseHandle.assert_called_with(103)


def test_task_run_windows_guardian_failure_prevents_launch(tmp_path):
    job, store = Mock(), Mock()
    job.protect_runner.side_effect = OSError("guardian denied")
    store.assign.return_value = {"id": "A-0001"}
    args = argparse.Namespace(task="T-0001", actor="test", role="builder", mode="rw", scope=["src"],
                              lease_seconds=None, rollout=None)
    with patch.object(task_runner.sys, "platform", "win32"), patch.object(signal, "SIGBREAK", 21, create=True), \
            patch.object(signal, "signal"), patch.object(task_runner, "Job", return_value=job), \
            patch.object(ledger, "Ledger", return_value=store), patch.object(task_runner, "_finish") as finish, \
            patch.object(subprocess, "Popen") as create:
        assert task_runner._execute(tmp_path / "unused.db", args, ["worker"], False, {'expected': str(tmp_path)}, 0) == 75
    create.assert_not_called()
    job.close.assert_called_once()
    assert finish.call_args.args[3] == 75
