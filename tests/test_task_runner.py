"""Native worker runner acceptance tests, discovered on all three CI operating systems."""

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from hipson import ledger, task_runner
from tests import test_agent_run as bash
from tests.support import ROOT, hipson_cli


class _NativeRun(bash._Run):
    def start(self, source, *, extra=(), stdin="", cwd=None, group_leader=False, before_main=""):
        worker = self.root / f"worker-{len(self.processes)}.py"
        worker.write_text(source, encoding="utf-8")
        input_path = self.root / f"stdin-{len(self.processes)}"
        input_path.write_text(stdin, encoding="utf-8")
        handles = [input_path.open("rb"), (self.root / f"stdout-{len(self.processes)}").open("wb"),
                   (self.root / f"stderr-{len(self.processes)}").open("wb")]
        self.handles.extend(handles)
        options = {"creationflags": 0 if self.env.get("RUNNER_TEST_CTRL_C") else subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {
            "process_group": 0 if group_leader else None}
        # Windows venv python.exe can be a launcher. Report the PID from the actual
        # runner interpreter, which owns the assignment and the Job handle.
        runner_pid = self.root / f"runner-{len(self.processes)}.pid"
        bootstrap = before_main + "\n" + ("import os; from pathlib import Path; from hipson.cli import main; "
                     f"Path({str(runner_pid)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
                     "raise SystemExit(main())")
        process = subprocess.Popen(
            [sys.executable, "-c", bootstrap, "task", "run", "--task", self.task, "--actor", "codex",
             "--role", "builder", "--mode", "rw", "--scope", "src", *extra, "--", sys.executable, str(worker)],
            cwd=cwd or self.root, env=self.env, stdin=handles[0], stdout=handles[1], stderr=handles[2], **options)
        self.processes.append(process)
        return process

    def wait(self, process):
        return process.wait(timeout=20)

    def ready(self, process):
        pids = _ready(self, process)
        owner = self.view()[0]["assignments"][0]["owner_pid"]
        assert all(os.getpgid(pid) == owner for pid in pids)
        return pids

    def kill(self, process):
        if sys.platform != "win32":
            process.kill()
            return
        from hipson.task_windows import Job
        # venv launchers can have a different PID from the interpreter owning
        # the Job. Terminate that owner, which is also the ledger's identity.
        path = self.root / f"runner-{self.processes.index(process)}.pid"
        if not path.exists():
            process.kill()
            return
        probe = Job()
        probe.kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        probe.kernel.TerminateProcess.restype = ctypes.c_int
        handle = probe.kernel.OpenProcess(1, False, int(path.read_text()))
        try:
            if handle:
                assert probe.kernel.TerminateProcess(handle, 1)
        finally:
            if handle:
                probe.kernel.CloseHandle(handle)
            probe.close()

    def cleanup(self):
        if sys.platform != "win32":
            return super().cleanup()
        for process in self.processes:
            if process.poll() is None:
                self.kill(process)
            process.wait(timeout=10)
        for handle in self.handles:
            handle.close()
        for pid in self.workers:
            bash._wait_for(lambda pid=pid: not ledger._pid_alive(pid), f"Job leaked process {pid}")


@contextmanager
def _case(root):
    with bash._subreaper():
        run = _NativeRun(root)
        try:
            yield run
        finally:
            run.cleanup()


def test_task_run_process_result_stdin_identity_and_refs(tmp_path):
    # This legacy test uses POSIX cleanup/liveness; replace only the harness on POSIX.
    # The same assertions below run natively on Windows as well.
    with _case(tmp_path) as run:
        process = run.start("""
import json, os, sys
from pathlib import Path
Path('received.json').write_text(json.dumps({'stdin': sys.stdin.read(), 'role': os.environ['HIPSON_ROLE'],
 'task': os.environ['HIPSON_TASK'], 'assignment': os.environ['HIPSON_ASSIGNMENT']}), encoding='utf-8')
print('worker output')
""", stdin="A brief\nsecond line\n", extra=("--ref", "a.md", "--ref", "b.md", "--lease-seconds", "3600"))
        assert run.wait(process) == 0, run.stderr(process)
        view, events = run.view()
        assignment = view["assignments"][0]
        runner_pid = int((tmp_path / "runner-0.pid").read_text())
        assert assignment["owner_pid"] == runner_pid
        if sys.platform != "win32":
            assert runner_pid == process.pid
        assert assignment["owner_host"] == socket.gethostname()
        assert assignment["owner_boot_id"] == ledger._boot_id()
        assert assignment["owner_pid_namespace"] == ledger._pid_namespace()
        assert assignment["lease_until"] is not None
        assert assignment["status"] == "passed" and view["claims"] == []
        assert json.loads((tmp_path / "received.json").read_text()) == {
            "stdin": "A brief\nsecond line\n", "role": "worker", "task": run.task, "assignment": assignment["id"]}
        assert events[-1]["refs"] == [assignment["id"], "a.md", "b.md"]
        assert "passed: worker process exit=0" in events[-1]["summary"]
        assert (tmp_path / "stdout-0").read_text() == "worker output\n"
        failed = run.start("raise SystemExit(7)")
        assert run.wait(failed) == 7, run.stderr(failed)
        assert "failed: worker process exit=7" in run.view()[1][-1]["summary"]


def test_task_run_result_ingestion_and_rejection_fallback(tmp_path):
    # Reuse the published contract assertions; no POSIX process inspection is needed.
    with patch.object(bash, "_supported", return_value=True), patch.object(bash, "_case", _case):
        bash.test_agent_run_result_files_preserve_outcomes_and_release_claims(tmp_path)


def test_task_run_project_binding_and_git_environment(tmp_path):
    with patch.object(bash, "_supported", return_value=True), patch.object(bash, "_case", _case):
        for index, test in enumerate((bash.test_runner_ignores_inherited_git_project_overrides,
                                     bash.test_audit_h03_runner_refuses_wrong_project_before_assignment,
                                     bash.test_audit_h03_runner_checks_non_git_cwd_and_nested_repositories)):
            root = tmp_path / str(index)
            root.mkdir()
            test(root)


def test_task_run_canonical_project_paths(tmp_path):
    if sys.platform == "win32":
        # Directory junctions need no symlink privilege on Windows.
        project = tmp_path / "project"
        project.mkdir()
        alias = tmp_path / "alias"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(project)], check=True, capture_output=True)
        try:
            with _case(project) as run:
                process = run.start("pass", cwd=alias)
                assert run.wait(process) == 0, run.stderr(process)
        finally:
            alias.rmdir()
    else:
        with patch.object(bash, "_case", _case):
            bash.test_audit_h03_runner_accepts_git_subdirectories_and_symlinks(tmp_path)


def test_task_run_usage_errors_and_default_read_only(tmp_path):
    cli = hipson_cli()
    assert cli
    env = {**os.environ, "HIPSON_LEDGER": str(tmp_path / "missing.sqlite")}
    for extra in (("--help",), ("-h",)):
        result = subprocess.run([cli, "task", "run", *extra], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and "--result-file" in result.stdout
        assert not (tmp_path / "missing.sqlite").exists()
    valid = ["--task", "T-0001", "--actor", "test", "--role", "builder"]
    for options in ([], valid, [*valid, "--mode", "wrong", "--", "echo"],
                    [*valid, "--mode", "rw", "--", "echo"], [*valid, "--rollout", "wrong", "--", "echo"],
                    [*valid, "--lease-seconds", "-1", "--", "echo"], [*valid, "--unknown", "--", "echo"],
                    [*valid, "--task"], [*valid, "--scope", "../escape", "--", "echo"]):
        result = subprocess.run([cli, "task", "run", *options], env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 64, (options, result.stderr)
        assert not (tmp_path / "missing.sqlite").exists()
    result = subprocess.run([cli, "task", "run", *valid, "--", "no-command"],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 75 and not (tmp_path / "missing.sqlite").exists()
    with closing(ledger.Ledger(tmp_path / "test.sqlite")) as store:
        task = store.open_task(project=tmp_path, title="RO", objective="Default mode")
    result = subprocess.run([cli, "task", "--db", str(tmp_path / "test.sqlite"), "run", "--task", task["id"],
                             "--actor", "test", "--role", "reviewer", "--", sys.executable, "-c", "print('ok')"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr
    with closing(ledger.Ledger(tmp_path / "test.sqlite")) as store:
        assert store.show(task["id"])["assignments"][0]["mode"] == "ro"

    # Shell-compatible command startup failures must still finish and release claims.
    for target, expected in ((tmp_path / "missing-command", 127), (tmp_path / "not-executable.txt", 126)):
        if expected == 126:
            target.write_text("not an executable", encoding="utf-8")
        result = subprocess.run([cli, "task", "run", "--db", str(tmp_path / "test.sqlite"), "--task", task["id"],
                                 "--actor", "test", "--role", "builder", "--mode", "rw", "--scope", "src",
                                 "--", str(target)], cwd=tmp_path, capture_output=True, text=True, timeout=10)
        assert result.returncode == expected, result.stderr
        with closing(ledger.Ledger(tmp_path / "test.sqlite")) as store:
            view = store.show(task["id"])
            assert view["assignments"][-1]["status"] == "failed" and view["claims"] == []
            assert f"worker process exit={expected}" in store.events(task["id"])[-1]["summary"]


def test_task_run_rollout_nonce_and_missing_rollout(tmp_path):
    with _case(tmp_path) as run:
        run.env["CODEX_HOME"] = str(tmp_path / "codex home")
        source = """
import json, os, re, sys, uuid
from pathlib import Path
brief = sys.stdin.read()
uuid.UUID(re.search(r'ledger-run-([^ ]+) -->', brief)[1])
assert brief.startswith('Worker brief')
sessions = Path(os.environ['CODEX_HOME']) / 'sessions'
sessions.mkdir(parents=True)
(sessions / 'unrelated.jsonl').write_text(json.dumps({'model': 'wrong'}), encoding='utf-8')
(sessions / 'ours.jsonl').write_text(json.dumps({'brief': brief, 'model': 'offline', 'reasoning_effort': 'high'}), encoding='utf-8')
"""
        process = run.start(source, stdin="Worker brief", extra=("--rollout", "codex"))
        assert run.wait(process) == 0, run.stderr(process)
        ending = run.view()[1][-1]
        assert "model=offline effort=high" in ending["summary"]
        assert ending["refs"][-1].endswith("ours.jsonl")
        process = run.start("pass", extra=("--rollout", "codex"))
        assert run.wait(process) == 0, run.stderr(process)
        assert "model=? effort=? (rollout not found)" in run.view()[1][-1]["summary"]


def _tree_source(leave_background=False):
    return """
import json, os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', '''
import signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.SIG_IGN)
Path('child-ready').touch()
time.sleep(120)
'''])
while not Path('child-ready').exists(): time.sleep(0.01)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.SIG_IGN)
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
""" + ("" if leave_background else "time.sleep(120)\n")


def _ready(run, process):
    path = run.root / "ready.json"
    bash._wait_for(lambda: path.exists() or process.poll() is not None, "Worker did not become ready")
    assert path.exists(), run.stderr(process)
    pids = json.loads(path.read_text())
    run.workers.extend(pids)
    return pids


def _stopped(pids):
    if sys.platform != "win32":
        bash._reap_adopted(pids)
    return not any(ledger._pid_alive(pid) for pid in pids)


def test_task_run_live_claim_and_tree_cleanup_before_finish(tmp_path):
    with _case(tmp_path) as run:
        holder = run.start(_tree_source())
        pids = _ready(run, holder)
        rejected = run.start("from pathlib import Path; Path('should-not-start').touch()")
        assert run.wait(rejected) == 75, run.stderr(rejected)
        assert not (tmp_path / "should-not-start").exists()
        assert len(run.view()[0]["claims"]) == 1
        if sys.platform == "win32":
            # CI may have no console. Allocate one before creating a new runner below;
            # hard-killing this holder exercises KILL_ON_JOB_CLOSE instead.
            run.kill(holder)
            run.wait(holder)
            bash._wait_for(lambda: _stopped(pids), "Job did not kill descendants after runner death")
            with closing(ledger.Ledger(run.db)) as store:
                assert store.reap()[0]["reason"] == "pid_gone"
        else:
            holder.terminate()
            ending = run.finished_after_stopping(pids)
            assert run.wait(holder) == 143
            assert "cancelled: worker process exit=TERM" in ending["summary"]
        assert run.view()[0]["claims"] == []


def test_task_run_leftovers_stop_before_claim_release(tmp_path):
    with _case(tmp_path) as run:
        process = run.start(_tree_source(leave_background=True))
        pids = _ready(run, process)
        # Observe completion as well as final state, guarding against premature release.
        def finished():
            endings = [e for e in run.view()[1] if e["type"] == "finished"]
            if endings:
                assert _stopped(pids), "Claims released while a worker could write"
                return endings[-1]
            return None
        ending = bash._wait_for(finished, "Runner never finished")
        assert run.wait(process) == 0, run.stderr(process)
        assert "leftover processes stopped" in ending["summary"]
        assert run.view()[0]["claims"] == []


def test_task_run_signal_forwarding(tmp_path):
    if sys.platform == "win32":
        # Isolate Ctrl+C broadcasts in a private console so CI/the user's console is untouched.
        for control in ("break", "interrupt", "escalate"):
            root = tmp_path / control
            root.mkdir()
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            result = subprocess.run([sys.executable, "-c", "from pathlib import Path; "
                                     "from tests.test_task_runner import _windows_signal_check; "
                                     f"_windows_signal_check(Path({str(root)!r}), {control!r})"],
                                    cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + str(ROOT / "src")},
                                    creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
    else:
        for sig in (signal.SIGINT, signal.SIGHUP):
            root = tmp_path / str(sig)
            root.mkdir()
            with _case(root) as run:
                process = run.start("""
import signal, time
from pathlib import Path
def stopped(sig, frame):
    Path('forwarded').touch()
    raise SystemExit(0)
signal.signal(signal.SIGINT, stopped)
signal.signal(signal.SIGHUP, stopped)
Path('ready.tmp').write_text('[]'); Path('ready.tmp').rename('ready.json')  # atomic: the test polls for this file
time.sleep(120)
""")
                _ready(run, process)
                process.send_signal(sig)
                assert run.wait(process) == 128 + sig, run.stderr(process)
                assert (root / "forwarded").exists()
                assert run.view()[0]["claims"] == []


def _windows_signal_check(root, control):
    signal.signal(signal.SIGINT, lambda *args: None)  # Private-console broadcast must not stop the test helper.
    with _case(root) as run:
        if control == "interrupt":
            run.env["RUNNER_TEST_CTRL_C"] = "1"
        source = _tree_source() if control == "escalate" else """
import signal, time
from pathlib import Path
def stopped(sig, frame):
    Path('forwarded').write_text(str(sig), encoding='utf-8')
    raise SystemExit(0)
signal.signal(signal.SIGBREAK, stopped)
Path('ready.tmp').write_text('[]'); Path('ready.tmp').rename('ready.json')  # atomic: the test polls for this file
# CPython only wakes a Windows sleep for SIGINT, not SIGBREAK. Return to Python
# regularly to dispatch the break handler within the runner's grace period.
while True: time.sleep(0.05)
"""
        process = run.start(source)
        pids = _ready(run, process)
        if control == "interrupt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            assert kernel.GenerateConsoleCtrlEvent(0, 0)  # CTRL_C_EVENT to our private console.
        else:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        expected = signal.SIGINT if control == "interrupt" else signal.SIGBREAK
        assert run.wait(process) == 128 + expected, run.stderr(process)
        assert _stopped(pids)
        if control != "escalate":
            assert (root / "forwarded").exists()
            assert (root / "forwarded").read_text() == str(int(signal.SIGBREAK))
        assert run.view()[0]["assignments"][0]["status"] == "cancelled"
        assert run.view()[0]["claims"] == []


def test_task_run_posix_recovery_and_zombies(tmp_path):
    # These POSIX-specific checks supplement the native Windows Job tests above.
    # The Windows branch runs the query-only liveness contract instead of skipping.
    if sys.platform == "win32":
        from tests.test_cross_platform import test_windows_pid_probe_only_queries_and_closes_handles
        test_windows_pid_probe_only_queries_and_closes_handles()
        return
    with patch.object(bash, "_case", _case):
        tests = [bash.test_agent_run_sigkill_retains_claim_until_surviving_worker_dies_then_reaps,
                 bash.test_agent_run_group_leader_without_own_session_stops_live_descendant]
        if sys.platform == "linux":
            tests.append(_native_zombie_check)
        for index, test in enumerate(tests):
            root = tmp_path / str(index)
            root.mkdir()
            test(root)


def _native_zombie_check(root):
    with _case(root) as run:
        process = run.start("""
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'pass'])
os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
while not Path('exit-worker').exists(): time.sleep(0.01)
os._exit(0)
""")
        _, grandchild = run.ready(process)
        status = Path(f"/proc/{grandchild}/status")
        assert "\nState:\tZ" in status.read_text()
        (root / "exit-worker").touch()
        assert run.wait(process) == 0, run.stderr(process)
        assert not status.exists(), "The Linux runner should reap its adopted zombie"
        view, events = run.view()
        assert view["claims"] == [] and view["assignments"][0]["status"] == "passed"
        assert events[-1]["type"] == "finished"


def test_task_run_missing_boot_identity_and_project_probe_failure(tmp_path):
    with _case(tmp_path) as run:
        bootstrap = """
from unittest.mock import patch
from hipson import cli, ledger
with patch.object(ledger, '_boot_id', return_value=None):
    raise SystemExit(cli.main())
"""
        result = subprocess.run([sys.executable, "-c", bootstrap, "task", "run", "--task", run.task,
                                 "--actor", "test", "--role", "builder", "--", sys.executable, "-c", "pass"],
                                cwd=tmp_path, env=run.env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assignment = run.view()[0]["assignments"][0]
        assert assignment["owner_boot_id"] is None and assignment["status"] == "passed"
        with patch.object(task_runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 10)):
            with patch.object(task_runner, "_execute", side_effect=AssertionError("must not assign")):
                from hipson.cli import main
                assert main(["task", "run", "--db", str(run.db), "--task", run.task, "--actor", "test",
                             "--role", "builder", "--", "no-command"]) == 76
        assert len(run.view()[0]["assignments"]) == 1


def test_task_run_windows_job_api_containment_and_fail_closed():
    from ctypes import wintypes
    from types import SimpleNamespace
    from unittest.mock import Mock

    from hipson.task_windows import Job, _Accounting, _ExtendedLimits, _ThreadEntry

    limits_seen = []
    def configure(handle, kind, pointer, size):
        limits_seen.append(ctypes.cast(pointer, ctypes.POINTER(_ExtendedLimits)).contents.basic.flags)
        assert kind == 9 and size == ctypes.sizeof(_ExtendedLimits)
        return True

    def thread_first(snapshot, pointer):
        entry = ctypes.cast(pointer, ctypes.POINTER(_ThreadEntry)).contents
        entry.pid, entry.tid = 42, 101
        return True

    def accounting(handle, kind, pointer, size, unused):
        ctypes.cast(pointer, ctypes.POINTER(_Accounting)).contents.active = 2
        return True

    kernel = SimpleNamespace(**{name: Mock(return_value=True) for name in (
        "CreateJobObjectW", "SetInformationJobObject", "QueryInformationJobObject", "AssignProcessToJobObject",
        "TerminateJobObject", "GenerateConsoleCtrlEvent", "OpenProcess", "GetCurrentProcess", "CloseHandle", "CreateToolhelp32Snapshot", "Thread32First",
        "Thread32Next", "OpenThread", "ResumeThread")})
    kernel.CreateJobObjectW.return_value = 0x123456789
    kernel.SetInformationJobObject.side_effect = configure
    kernel.OpenProcess.return_value = 0x223456789
    kernel.CreateToolhelp32Snapshot.return_value = 0x323456789
    kernel.Thread32First.side_effect = thread_first
    kernel.OpenThread.return_value = 0x423456789
    kernel.ResumeThread.return_value = 1
    kernel.QueryInformationJobObject.side_effect = accounting
    with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
        job = Job()
        assert limits_seen == [0x2000]
        kernel.CreateJobObjectW.assert_called_once_with(None, None)
        assert kernel.CreateJobObjectW.restype is wintypes.HANDLE
        assert kernel.OpenThread.restype is wintypes.HANDLE
        job.attach(SimpleNamespace(pid=42))
        kernel.AssignProcessToJobObject.assert_called_once_with(0x123456789, 0x223456789)
        kernel.ResumeThread.assert_called_once_with(0x423456789)
        assert kernel.CloseHandle.call_args_list[:3] == [((0x223456789,),), ((0x423456789,),), ((0x323456789,),)]
        assert job.active()
        job.interrupt(42)
        kernel.GenerateConsoleCtrlEvent.assert_called_once_with(1, 42)
        kernel.GenerateConsoleCtrlEvent.return_value = False
        with patch.object(ctypes, "WinError", side_effect=OSError("no shared console"), create=True):
            try:
                job.interrupt(42)
            except OSError as exc:
                assert "no shared console" in str(exc)
            else:
                raise AssertionError("Console delivery failures must be reported")
        job.terminate()
        kernel.TerminateJobObject.assert_called_once_with(0x123456789, 1)
        job.close()
        assert job.handle is None
        kernel.CloseHandle.assert_called_with(0x123456789)
        kernel.AssignProcessToJobObject.return_value = False
        kernel.ResumeThread.reset_mock()
        with patch.object(ctypes, "WinError", side_effect=OSError("job denied"), create=True):
            job = Job()
            try:
                job.attach(SimpleNamespace(pid=42))
            except OSError:
                pass
            else:
                raise AssertionError("A worker must not run outside its job")
            kernel.ResumeThread.assert_not_called()
            job.close()


def test_task_run_windows_console_failure_still_stops_job():
    from unittest.mock import Mock

    job, child = Mock(), Mock()
    job.interrupt.side_effect = OSError("no shared console")
    job.active.side_effect = [True, True, False, False]
    with patch.object(task_runner, "_message") as message:
        assert task_runner._stop(child, job, signal.SIGTERM, 0)
    job.interrupt.assert_called_once_with(child.pid)
    job.terminate.assert_called_once_with()
    message.assert_called_once_with(
        "could not forward Ctrl+Break: no shared console; enforcing Job cleanup after grace")


def test_task_run_rollout_cancel_with_stdin_still_open(tmp_path):
    if sys.platform == "win32":
        # The private-console helper tests cancellation after assignment on Windows.
        # Here verify a normal piped brief on Windows too, without a skip exception.
        with _case(tmp_path) as run:
            run.env["CODEX_HOME"] = str(tmp_path / "no sessions")
            process = run.start("import sys; assert 'ledger-run-' in sys.stdin.read()",
                                stdin="Piped brief", extra=("--rollout", "codex"))
            assert run.wait(process) == 0, run.stderr(process)
        return
    with _case(tmp_path) as run:
        process = subprocess.Popen([sys.executable, "-m", "hipson.cli", "task", "run", "--task", run.task,
                                    "--actor", "test", "--role", "builder", "--mode", "rw", "--scope", "src",
                                    "--rollout", "codex", "--", sys.executable, "-c", "raise SystemExit(99)"],
                                   cwd=tmp_path, env=run.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run.processes.append(process)
        try:
            bash._wait_for(lambda: bool(run.view()[0]["assignments"]), "Runner did not assign")
            process.terminate()
            assert process.wait(timeout=10) == 143
            assert "cancelled: worker process exit=TERM" in run.view()[1][-1]["summary"]
            assert run.view()[0]["claims"] == []
        finally:
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
