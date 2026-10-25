"""Published worker-runner integration tests; also runnable by scripts/run_tests.py."""

import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from unittest import SkipTest
from unittest.mock import patch

from hipson.ledger import Ledger
from tests.support import hipson_cli

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "examples" / "agent-run.sh"


def _hipson_bin():
    return hipson_cli()


def _skip(reason):
    if sys.platform == "linux" and os.environ.get("CI"):
        raise AssertionError(f"Required Linux CI integration prerequisite missing: {reason}")
    # Both pytest and the dependency-free runner understand this stdlib signal.
    raise SkipTest(reason)


def _supported():
    if sys.platform != "linux" or any(shutil.which(tool) is None for tool in ("bash", "setsid", "ps")):
        return _skip("requires Linux process groups, setsid, ps and bash")
    if _hipson_bin() is None:
        return _skip("requires an executable hipson CLI (HIPSON_BIN, PATH, or checkout .venv)")
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if not hasattr(libc, "prctl") or libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        return _skip("requires prctl child-subreaper support")
    if libc.prctl(36, previous.value, 0, 0, 0) != 0:
        return _skip("child-subreaper configuration is unavailable")
    return True


def _wait_for(predicate, message, *, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError(message)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _reap_adopted(pids):
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass  # Still belongs to the runner or worker, or was already reaped.


@contextmanager
def _subreaper():
    # Reap adopted descendants like an init would; container PID 1 may leave zombies forever.
    # Only reap known worker PIDs, never subprocess.Popen's direct children.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0  # PR_GET_CHILD_SUBREAPER
    assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    try:
        yield
    finally:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


class _Run:
    def __init__(self, root):
        self.root = root
        self.db = root / "ledger.sqlite"
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "HIPSON_LEDGER": str(self.db),
            "HIPSON_BIN": _hipson_bin(),
            "AGENT_RUN_GRACE_SECONDS": "1",
        }
        opened = subprocess.run(
            [self.env["HIPSON_BIN"], "task", "open", "--project", str(root), "--title", "Runner test",
             "--objective", "Verify the published runner", "--json"],
            env=self.env, cwd=root, text=True, capture_output=True, timeout=15, check=True,
        )
        self.task = json.loads(opened.stdout)["id"]
        self.processes = []
        self.workers = []
        self.handles = []

    def start(self, source, *, stdin="", extra=(), group_leader=False):
        index = len(self.processes)
        worker = self.root / f"worker-{index}.py"
        worker.write_text(source, encoding="utf-8")
        input_path = self.root / f"stdin-{index}"
        input_path.write_text(stdin, encoding="utf-8")
        handles = [input_path.open(), (self.root / f"stdout-{index}").open("w"),
                   (self.root / f"stderr-{index}").open("w")]
        self.handles.extend(handles)
        process = subprocess.Popen(
            ["bash", str(RUNNER), "--task", self.task, "--actor", "codex", "--role", "builder",
             "--mode", "rw", "--scope", "src", *extra, "--", sys.executable, str(worker)],
            cwd=self.root, env=self.env, stdin=handles[0], stdout=handles[1], stderr=handles[2],
            process_group=0 if group_leader else None,
        )
        self.processes.append(process)
        return process

    def wait(self, process):
        def completed():
            _reap_adopted(self.workers)
            return process.poll() is not None
        _wait_for(completed, f"Runner {process.pid} did not exit: {self.stderr(process)}")
        return process.returncode

    def stderr(self, process):
        return (self.root / f"stderr-{self.processes.index(process)}").read_text()

    def ready(self, process):
        path = self.root / "ready.json"
        _wait_for(lambda: path.exists() or process.poll() is not None, "Worker never became ready")
        assert path.exists(), self.stderr(process)
        pids = json.loads(path.read_text())
        self.workers.extend(pids)
        assert all(os.getpgid(pid) == process.pid for pid in pids)
        return pids

    def view(self):
        with closing(Ledger(self.db, create=False, read_only=True)) as store:
            return store.show(self.task), store.events(self.task)

    def finished_after_stopping(self, pids):
        def finished():
            _reap_adopted(pids)
            _, events = self.view()
            endings = [event for event in events if event["type"] == "finished"]
            if endings:
                assert not any(_alive(pid) for pid in pids), "Finished while a worker could still write"
                return endings[-1]
            return None
        return _wait_for(finished, "Runner never recorded finished after stopping its descendants")

    def cleanup(self):
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        for pid in self.workers:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _wait_for(lambda: (_reap_adopted(self.workers), not any(_alive(pid) for pid in self.workers))[1],
                  "Worker process leaked during test cleanup")
        for handle in self.handles:
            handle.close()


@contextmanager
def _case(root):
    with _subreaper():
        run = _Run(root)
        try:
            yield run
        finally:
            run.cleanup()


def _tree_worker(*, leave_background=False):
    return """
import json, os, signal, subprocess, sys
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'])
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
""" + ("" if leave_background else "while True: signal.pause()\n")


def test_agent_run_help_succeeds_without_opening_ledger(tmp_path: Path):
    db = tmp_path / "missing.sqlite"
    for flag in ("--help", "-h"):
        proc = subprocess.run(["bash", str(RUNNER), flag], env={**os.environ, "HIPSON_LEDGER": str(db)},
                              capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, proc.stderr
        assert "Usage:" in proc.stdout and "--result-file" in proc.stdout and "--lease-seconds" in proc.stdout
        assert not db.exists()


def test_agent_run_records_passed_failed_and_forwards_stdin(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        source = """
import json, os, sys
from pathlib import Path
Path('received.json').write_text(json.dumps({
    'stdin': sys.stdin.read(), 'role': os.environ['HIPSON_ROLE'],
    'task': os.environ['HIPSON_TASK'], 'assignment': os.environ['HIPSON_ASSIGNMENT'],
}))
print('worker output')
"""
        process = run.start(source, stdin="A worker brief\nwith a second line\n", extra=("--ref", "report.md"))
        assert run.wait(process) == 0, run.stderr(process)
        view, events = run.view()
        assignment = view["assignments"][0]
        assert assignment["owner_pid_namespace"] == os.readlink("/proc/self/ns/pid")
        assert assignment["status"] == "passed" and view["claims"] == []
        assert json.loads((tmp_path / "received.json").read_text()) == {
            "stdin": "A worker brief\nwith a second line\n", "role": "worker",
            "task": run.task, "assignment": assignment["id"],
        }
        assert (tmp_path / "stdout-0").read_text() == "worker output\n"
        assert events[-1]["type"] == "finished" and "passed: worker process exit=0" in events[-1]["summary"]
        assert events[-1]["refs"] == [assignment["id"], "report.md"]
        failed = run.start("raise SystemExit(7)\n")
        assert run.wait(failed) == 7, run.stderr(failed)
        view, events = run.view()
        assert [item["status"] for item in view["assignments"]] == ["passed", "failed"]
        assert view["claims"] == []
        assert "failed: worker process exit=7" in events[-1]["summary"]


def test_agent_run_result_files_preserve_outcomes_and_release_claims(tmp_path: Path):
    if not _supported():
        return
    report = {
        "status": "blocked", "summary": "Worker needs follow-up", "files_changed": [],
        "commands": [{"command": "worker check", "exit_code": 0}], "findings": [], "risks": [],
    }
    with _case(tmp_path) as run:
        for label, content, exit_code, rejected in (
            ("valid", json.dumps(report), 0, False),
            ("valid-failed-process", json.dumps(report), 7, False),
            ("invalid-json", "{broken", 0, True),
            ("invalid-schema", '{"status":"completed"}', 7, True),
            ("empty", "", 0, False),
            ("missing", None, 7, False),
        ):
            result_file = tmp_path / f"{label} result.json"
            source = "from pathlib import Path\n"
            if content is not None:
                source += f"Path({str(result_file)!r}).write_text({content!r})\n"
            source += f"raise SystemExit({exit_code})\n"
            process = run.start(source, extra=("--result-file", str(result_file), "--ref", "evidence.md"))
            assert run.wait(process) == exit_code, run.stderr(process)
            view, events = run.view()
            assignment = view["assignments"][-1]
            assert assignment["status"] == ("passed" if exit_code == 0 else "failed")
            assert view["claims"] == []
            assert assignment["result"] == (report if label.startswith("valid") else None)
            finished = [event for event in events if event["type"] == "finished"]
            assert len(finished) == len(view["assignments"])
            assert ("result file rejected" in finished[-1]["summary"]) == rejected
            assert f"worker process exit={exit_code}" in finished[-1]["summary"]
            assert finished[-1]["refs"] == [assignment["id"], "evidence.md"]


def test_agent_run_live_claim_refuses_worker_with_exit_75(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        holder = run.start(_tree_worker())
        run.ready(holder)
        rejected = run.start("from pathlib import Path\nPath('should-not-start').touch()\n")
        assert run.wait(rejected) == 75, run.stderr(rejected)
        assert not (tmp_path / "should-not-start").exists()
        assert "worker not started" in run.stderr(rejected)
        view, events = run.view()
        assert len(view["assignments"]) == len(view["claims"]) == 1
        assert view["assignments"][0]["owner_pid"] == holder.pid
        assert events[-1]["type"] == "claim_conflict"


def test_agent_run_sigterm_stops_term_ignoring_worker_and_grandchild_before_finish(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        process = run.start(_tree_worker())
        pids = run.ready(process)
        process.terminate()
        ending = run.finished_after_stopping(pids)
        assert run.wait(process) == 143, run.stderr(process)
        assert "cancelled: worker process exit=TERM" in ending["summary"]
        view, _ = run.view()
        assert view["assignments"][0]["status"] == "cancelled" and view["claims"] == []


def test_agent_run_stops_leftover_background_process_and_records_it(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        process = run.start(_tree_worker(leave_background=True))
        # The worker can already have exited; only the grandchild must still be in the group.
        _wait_for(lambda: (tmp_path / "ready.json").exists(), "Worker did not start its background process")
        pids = json.loads((tmp_path / "ready.json").read_text())
        run.workers.extend(pids)
        ending = run.finished_after_stopping(pids)
        assert run.wait(process) == 0, run.stderr(process)
        assert "passed: worker process exit=0" in ending["summary"]
        assert "leftover processes stopped" in ending["summary"]
        assert "worker left processes running" in run.stderr(process)
        view, _ = run.view()
        assert view["claims"] == [] and view["assignments"][0]["status"] == "passed"


def test_agent_run_group_leader_without_own_session_stops_live_descendant(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        process = run.start("""
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', '''
import signal
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('child-ready').touch()
while True: signal.pause()
'''])
while not Path('child-ready').exists(): time.sleep(0.01)
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
while not Path('exit-worker').exists(): time.sleep(0.01)
""", group_leader=True)
        pids = run.ready(process)
        assert os.getpgid(process.pid) == process.pid
        assert os.getsid(process.pid) != process.pid, "Runner must lead a group within another session"
        (tmp_path / "exit-worker").touch()
        ending = run.finished_after_stopping(pids)
        assert run.wait(process) == 0, run.stderr(process)
        assert "leftover processes stopped" in ending["summary"]
        assert "worker left processes running" in run.stderr(process)
        assert "survived KILL" not in run.stderr(process)
        view, _ = run.view()
        assert view["claims"] == [] and view["assignments"][0]["status"] == "passed"


def test_agent_run_unreaped_zombie_grandchild_does_not_prevent_finish(tmp_path: Path):
    if not _supported():
        return
    if not hasattr(os, "waitid") or not hasattr(os, "WNOWAIT"):
        _skip("requires waitid WNOWAIT to leave a known exited child unreaped")
        return
    with _case(tmp_path) as run:
        process = run.start("""
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'pass'])
os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
Path('ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
Path('ready.tmp').rename('ready.json')
while not Path('exit-worker').exists(): time.sleep(0.01)
os._exit(0)  # Preserve the zombie for adoption by the test's subreaper.
""")
        _, grandchild = run.ready(process)
        status = Path(f"/proc/{grandchild}/status")
        assert "\nState:\tZ" in status.read_text()
        (tmp_path / "exit-worker").touch()
        # Deliberately bypass run.wait/finished_after_stopping: both reap adopted children.
        assert process.wait(timeout=15) == 0, run.stderr(process)
        info = status.read_text()
        assert "\nState:\tZ" in info and f"\nPPid:\t{os.getpid()}\n" in info
        assert "survived KILL" not in run.stderr(process)
        view, events = run.view()
        assert view["claims"] == [] and view["assignments"][0]["status"] == "passed"
        assert events[-1]["type"] == "finished" and "passed: worker process exit=0" in events[-1]["summary"]


def test_agent_run_sigkill_retains_claim_until_surviving_worker_dies_then_reaps(tmp_path: Path):
    if not _supported():
        return
    with _case(tmp_path) as run:
        process = run.start(_tree_worker())
        pids = run.ready(process)
        assignment = run.view()[0]["assignments"][0]["id"]
        process.kill()
        assert run.wait(process) == -signal.SIGKILL
        assert all(_alive(pid) for pid in pids)
        rejected = run.start("from pathlib import Path\nPath('should-not-start').touch()\n")
        assert run.wait(rejected) == 75, run.stderr(rejected)
        assert not (tmp_path / "should-not-start").exists()
        view, events = run.view()
        assert view["claims"] == [{"assignment": assignment, "path": "src"}]
        assert view["assignments"][0]["status"] == "active"
        assert not any(event["type"] in ("finished", "reaped") for event in events)
        os.killpg(process.pid, signal.SIGKILL)
        _wait_for(lambda: (_reap_adopted(pids), not any(_alive(pid) for pid in pids))[1], "Workers survived KILL")
        following = run.start("from pathlib import Path\nPath('next-worker-started').touch()\n")
        assert run.wait(following) == 0, run.stderr(following)
        assert (tmp_path / "next-worker-started").exists()
        view, events = run.view()
        assert [item["status"] for item in view["assignments"]] == ["cancelled", "passed"]
        reaped = [event for event in events if event["type"] == "reaped"]
        assert len(reaped) == 1 and reaped[0]["refs"] == [assignment]
        assert "pid_gone" in reaped[0]["summary"]
        assert view["claims"] == []


def test_agent_run_missing_boot_id_file(tmp_path: Path):
    if not _supported():
        return
    # Intercept only the unavailable procfs read; leave the published wrapper unchanged.
    # This also proves that an omitted boot id still permits a real CLI assignment/finish.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_cat = shutil.which("cat")
    assert real_cat is not None
    cat = bin_dir / "cat"
    cat.write_text(
        '#!/usr/bin/env bash\n'
        'if [ "${1:-}" = /proc/sys/kernel/random/boot_id ]; then exit 1; fi\n'
        'exec "$AGENT_TEST_REAL_CAT" "$@"\n',
        encoding="utf-8",
    )
    cat.chmod(0o755)
    with _case(tmp_path) as run:
        run.env.update(PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), AGENT_TEST_REAL_CAT=real_cat)
        process = run.start("print('worker ran without boot id')\n")
        assert run.wait(process) == 0, run.stderr(process)
        view, events = run.view()
        assignment = view["assignments"][0]
        assert assignment["owner_boot_id"] is None
        assert assignment["owner_pid"] == process.pid
        assert assignment["status"] == "passed" and view["claims"] == []
        assert events[-1]["type"] == "finished"
        assert (tmp_path / "stdout-0").read_text() == "worker ran without boot id\n"


def test_agent_run_cli_resolution():
    with patch.dict(os.environ, {"HIPSON_BIN": "/explicit/hipson"}), patch.object(shutil, "which") as which:
        which.return_value = "/explicit/hipson"
        assert _hipson_bin() == "/explicit/hipson"
        which.assert_called_once_with("/explicit/hipson")
    with patch.dict(os.environ, {"HIPSON_BIN": ""}), patch.object(shutil, "which") as which:
        which.side_effect = ["/installed/hipson", "/installed/hipson"]
        assert _hipson_bin() == "/installed/hipson"
    with patch.dict(os.environ, {"HIPSON_BIN": ""}), patch.object(shutil, "which") as which:
        which.side_effect = [None, str(ROOT / ".venv" / "bin" / "hipson")]
        assert _hipson_bin() == str(ROOT / ".venv" / "bin" / "hipson")
        which.assert_called_with(str(ROOT / ".venv" / "bin" / "hipson"))


def test_agent_run_missing_prerequisites_fail_in_linux_ci():
    with patch.dict(os.environ, {"CI": "true"}), patch.object(sys, "platform", "linux"):
        try:
            _skip("missing prerequisite")
        except AssertionError as exc:
            assert "Required Linux CI" in str(exc)
        else:
            raise AssertionError("Missing Linux CI prerequisites must fail")
