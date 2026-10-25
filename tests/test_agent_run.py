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

from hipson.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "examples" / "agent-run.sh"
HIPSON = ROOT / ".venv" / "bin" / "hipson"


def _skip(reason):
    # Keep the dependency-free runner usable while giving pytest a real skip result.
    if "pytest" in sys.modules:
        sys.modules["pytest"].skip(reason)
    print(f"SKIP agent-run: {reason}")
    return False


def _supported():
    if sys.platform != "linux" or any(shutil.which(tool) is None for tool in ("bash", "setsid", "ps")):
        return _skip("requires Linux process groups, setsid, ps and bash")
    if not HIPSON.is_file():
        return _skip("requires this checkout's .venv/bin/hipson CLI")
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
            "PATH": str(ROOT / ".venv" / "bin") + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "src"),
            "HIPSON_LEDGER": str(self.db),
            "HIPSON_BIN": str(HIPSON),
            "AGENT_RUN_GRACE_SECONDS": "1",
        }
        opened = subprocess.run(
            [str(HIPSON), "task", "open", "--project", str(root), "--title", "Runner test",
             "--objective", "Verify the published runner", "--json"],
            env=self.env, cwd=root, text=True, capture_output=True, timeout=15, check=True,
        )
        self.task = json.loads(opened.stdout)["id"]
        self.processes = []
        self.workers = []
        self.handles = []

    def start(self, source, *, stdin="", extra=()):
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


def test_agent_run_missing_boot_id_file():
    _skip("agent-run.sh has no boot-id path override; cannot simulate a missing file without replacing cat")
