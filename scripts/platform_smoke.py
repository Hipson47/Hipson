#!/usr/bin/env python3
"""Exercise the installed Hipson CLI in a disposable Git project."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
HOLDER = (
    "import sys, time; from pathlib import Path\n"
    "ready, release = map(Path, sys.argv[1:])\n"
    "ready.touch()\n"
    "deadline = time.monotonic() + 120\n"
    "while not release.exists() and time.monotonic() < deadline:\n"
    "    time.sleep(0.1)\n"
    "raise SystemExit(0 if release.exists() else 1)\n"
)


def mask_private(text: str, home: str, login: str, *, windows: bool = False) -> str:
    # Paths can appear in command output, JSON strings, or Git's forward-slash notation.
    variants = (home, home.replace("\\", "/"))
    homes = {variant for path in variants for variant in (path, json.dumps(path)[1:-1],
                                                          json.dumps(path, ensure_ascii=False)[1:-1])}
    flags = re.IGNORECASE if windows else 0
    replacements = [(path, "~") for path in sorted(homes, key=len, reverse=True)] + [(login, "<user>")]
    for value, mask in replacements:
        if len(value) > 1:
            text = re.sub(re.escape(value), mask, text, flags=flags)
    return text


def failure_detail(output: str) -> str:
    return "\n".join(f"      {line}" for line in output.splitlines()[-15:])


def boot_identity_skip(system: str) -> str | None:
    if system not in {"Linux", "Darwin"}:
        return f"skipped on {system}: boot identity is available only on Linux and macOS"
    return None


def term_forwarding_skip(system: str) -> str | None:
    if system == "Windows":
        return f"skipped on {system}: TERM forwarding requires POSIX signals"
    return None


class SmokeError(Exception):
    """A check did not produce the expected result."""


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise SmokeError(detail)


class Report:
    def __init__(self, stream: TextIO, console: TextIO):
        self.stream = stream
        self.console = console
        self.home = str(Path.home())
        self.login = getpass.getuser()
        self.passed = 0
        self.failed: list[str] = []

    def log(self, line: str = "", *, terminal: bool = True) -> None:
        masked = mask_private(line, self.home, self.login, windows=sys.platform == "win32")
        print(masked, file=self.stream, flush=True)
        if terminal:
            print(masked, file=self.console, flush=True)

    def success(self, name: str) -> None:
        self.passed += 1
        self.log(f"PASS  {name}")

    def failure(self, name: str, detail: str = "") -> None:
        self.failed.append(name)
        self.log(f"FAIL  {name}")
        if detail:
            self.log(failure_detail(detail))

    @contextmanager
    def checking(self, name: str):
        try:
            yield
        except (SmokeError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            self.failure(name, str(exc))
        else:
            self.success(name)

    def finish(self) -> int:
        self.log()
        self.log(f"RESULT: {self.passed} passed, {len(self.failed)} failed")
        if self.failed:
            self.log("Failed checks:\n" + "\n".join(f"  - {name}" for name in self.failed))
        return int(bool(self.failed))


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part.rstrip() for part in (result.stdout, result.stderr) if part)


def command_json(result: subprocess.CompletedProcess[str]):
    require(result.returncode == 0, f"exit {result.returncode}\n{output_of(result)}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise SmokeError(f"{exc}\n{output_of(result)}") from exc


def environment(work: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("HIPSON_ROLE", "HIPSON_TASK", "HIPSON_ASSIGNMENT", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    env.update(HIPSON_LEDGER=str(work / "ledger.sqlite"), HIPSON_HOME=str(work / "hipson-home"),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    return env


class Smoke:
    def __init__(self, cli: str, work: Path, report: Report):
        self.cli = cli
        self.work = work
        self.project = work / "project"
        self.report = report
        self.env = environment(work)

    def run(self, *argv, cwd: Path | None = None, env: dict[str, str] | None = None,
            stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=cwd or self.project, env=self.env if env is None else env,
                              input=stdin, stdin=subprocess.DEVNULL if stdin is None else None,
                              capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)

    def hipson(self, *argv, **kwargs) -> subprocess.CompletedProcess[str]:
        return self.run(self.cli, *argv, **kwargs)

    def record_exit(self, name: str, result: subprocess.CompletedProcess[str], *, expected: int = 0) -> None:
        if result.returncode == expected:
            self.report.success(name)
        else:
            self.report.failure(name, f"Exit {result.returncode}, expected {expected}\n{output_of(result)}")

    def check(self, name: str, *argv, **kwargs) -> None:
        try:
            result = self.hipson(*argv, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            self.report.failure(name, str(exc))
        else:
            self.record_exit(name, result)

    def create_project(self) -> None:
        self.project.mkdir()
        source = self.project / "src/parser/pairs.py"
        source.parent.mkdir(parents=True)
        source.write_text("def parse_pair(text):\n    return tuple(text.split(','))\n", encoding="utf-8")
        (self.project / "acceptance.py").write_text(
            "from src.parser.pairs import parse_pair\n"
            "assert parse_pair(' a : b ') == ('a', 'b')\n",
            encoding="utf-8",
        )
        # Keep fixture commits independent of any user hooks, signing, or Git templates.
        self.env.update(GIT_CONFIG_GLOBAL=str(self.work / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        for args in (
            ("init", "-q", "--template="),
            ("add", "."),
            ("-c", "user.name=Smoke", "-c", "user.email=smoke@example.com", "commit", "-qm", "Initial"),
        ):
            result = self.run("git", *args)
            require(result.returncode == 0, output_of(result))

    def open_task(self, title: str, objective: str, *args) -> str:
        result = self.hipson("task", "open", "--project", ".", "--title", title, "--objective", objective,
                             *args, "--json")
        return command_json(result)["id"]

    def manual_lifecycle(self) -> None:
        task = self.open_task("Fix parser", "Parser accepts trailing commas", "--acceptance", "tests pass")
        self.report.success("task open")
        self.check("task assign (rw claim)", "task", "assign", "--task", task, "--actor", "codex",
                   "--role", "builder", "--mode", "rw", "--scope", "src/parser")
        self.check("task board", "task", "board")
        self.check("task finish", "task", "finish", "--assignment", "A-0001", "--result", "passed",
                   "--summary", "worker exited 0", "--ref", "runs/report.md")
        self.check("task ack", "task", "ack", "--assignment", "A-0001",
                   "--note", "Read worker outcome and independently checked the change")
        self.check("task verify", "task", "verify", "--task", task, "--", "git", "diff", "--check")
        self.check("task status -> review", "task", "status", "--task", task, "--to", "review",
                   "--actor", "lead", "--summary", "verified: tests pass")
        self.check("task status -> done", "task", "status", "--task", task, "--to", "done", "--actor", "lead")
        self.check("task brief", "task", "brief", "--task", task, "--role", "reviewer")

    def task_view(self, task: str):
        return command_json(self.hipson("task", "show", "--task", task, "--json"))

    def reap(self, task: str):
        return command_json(self.hipson("task", "reap", "--task", task, "--json"))

    def runner_command(self) -> list[str]:
        if sys.platform != "win32":
            return [self.cli]
        # Windows console scripts and venv python.exe can both be launchers.
        # Start the installed entry point in the base interpreter so the Popen
        # handle belongs to the ledger owner and the Job closes when it is killed.
        scripts = Path(self.cli).parent
        python = next((path for path in (scripts / "python.exe", scripts.parent / "python.exe") if path.is_file()),
                      Path(PYTHON))
        base, site_packages = command_json(self.run(
            python, "-c", "import json, sys, sysconfig; from pathlib import Path; "
            "user_scripts = Path(sysconfig.get_path('scripts', scheme='nt_user')); "
            "purelib = sysconfig.get_path('purelib', scheme='nt_user') "
            "if Path(sys.argv[1]) == user_scripts else sysconfig.get_path('purelib'); "
            "print(json.dumps([sys._base_executable, purelib]))", scripts,
        ))
        return [base, "-I", "-S", "-c",
                "import site, sys; site.addsitedir(sys.argv.pop(1)); "
                "from hipson.cli import main; raise SystemExit(main())", site_packages]

    def holder_assignment(self, task: str, actor: str, child: subprocess.Popen, ready: Path, log: Path):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and child.poll() is None:
            if ready.exists():
                view = self.task_view(task)
                for assignment in view["assignments"]:
                    if assignment["actor"] == actor and assignment["status"] == "active":
                        require({"assignment": assignment["id"], "path": "src"} in view["claims"],
                                "holder has no write claim for src")
                        return assignment
            time.sleep(0.1)
        raise SmokeError(f"holder did not become ready (exit {child.poll()})\n{log.read_text(encoding='utf-8')}")

    def worker_lifecycle(self, runner: list[str], *, crash: bool) -> None:
        scenario = "crash recovery" if crash else "native runner"
        actor = "holder" if crash else "native-holder"
        task = self.open_task(scenario, "Supervise a worker holding a write claim")
        run = ("task", "run", "--task", task)
        scope = ("--role", "builder", "--mode", "rw", "--scope", "src")
        ready = self.work / f"{actor}.ready"
        release = self.work / f"{actor}.release"
        log = self.work / f"{actor}.log"
        system = platform.system()
        with log.open("wb") as output, subprocess.Popen(
            [*runner, *run, "--actor", actor, *scope, "--", PYTHON, "-c", HOLDER, ready, release],
            cwd=self.project, env=self.env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        ) as holder:
            try:
                assignment = self.holder_assignment(task, actor, holder, ready, log)
                if crash:
                    with self.report.checking("recorded owner is the runner this script started"):
                        require(assignment["owner_pid"] == holder.pid,
                                f"owner_pid={assignment['owner_pid']}, started={holder.pid}")
                    if skipped := boot_identity_skip(system):
                        self.report.log(skipped)
                    else:
                        boot = assignment["owner_boot_id"]
                        if isinstance(boot, str) and boot.strip():
                            self.report.success(f"boot identity available ({boot})")
                        else:
                            self.report.failure("boot identity available", "Runner recorded no boot identity")

                name = ("second writer on the same scope refused" if crash else "native runner refuses overlapping writer")
                second = self.hipson(*run, "--actor", "second", *scope, "--", PYTHON, "-c", "pass")
                self.record_exit(f"{name} (exit 75)", second, expected=75)

                if crash:
                    with self.report.checking("reap keeps a live worker"):
                        reaped = self.reap(task)
                        view = self.task_view(task)
                        require(not reaped and holder.poll() is None and any(
                            item["id"] == assignment["id"] and item["status"] == "active"
                            for item in view["assignments"]
                        ) and {"assignment": assignment["id"], "path": "src"} in view["claims"],
                            f"reap={reaped!r}; assignments={view['assignments']!r}; claims={view['claims']!r}")
                    hard_kill(holder)
                elif skipped := term_forwarding_skip(system):
                    self.report.log(skipped)
                    release.touch()
                else:
                    os.kill(holder.pid, signal.SIGTERM)

                code = holder.wait(timeout=30)
                if not crash:
                    expected = 0 if system == "Windows" else 128 + signal.SIGTERM
                    name = "native runner finishes released worker" if system == "Windows" else "native runner forwards TERM (exit 143)"
                    if code == expected:
                        self.report.success(name)
                    else:
                        self.report.failure(f"{name} (got {code})", log.read_text(encoding="utf-8", errors="replace"))
            finally:
                hard_kill(holder)

        if crash:
            with self.report.checking("reap releases a killed worker's claim"):
                deadline = time.monotonic() + 10
                reaped = self.reap(task)
                # On POSIX the worker may still be exiting when the runner's wait completes.
                while not reaped and time.monotonic() < deadline:
                    time.sleep(0.1)
                    reaped = self.reap(task)
                require(any(item["reason"] == "pid_gone" and item["assignment"]["id"] == assignment["id"]
                            and item["assignment"]["status"] == "cancelled" for item in reaped)
                        and not self.task_view(task)["claims"], repr(reaped))
            self.check("scope reusable after reap", *run, "--actor", "third", *scope, "--", PYTHON, "-c", "pass")
            return

        self.check("native runner scope reusable, stdin and refs", *run, "--actor", "native-next", *scope,
                   "--ref", "runs/report.md", "--", PYTHON, "-c",
                   'import sys; assert sys.stdin.read() == "brief\\n"', stdin="brief\n")
        failed = self.hipson(*run, "--actor", "native-failed", "--role", "reviewer", "--",
                             PYTHON, "-c", "raise SystemExit(9)")
        self.record_exit("native runner preserves failed child code (exit 9)", failed, expected=9)
        mismatch = self.hipson(*run, "--actor", "wrong-project", "--role", "reviewer", "--",
                               PYTHON, "-c", "pass", cwd=self.work)
        self.record_exit("native runner binds project (exit 76)", mismatch, expected=76)

    def short_flow(self) -> None:
        check = shlex.join([Path(PYTHON).as_posix(), "-c", "pass"])
        with self.report.checking("task close runs the declared check"):
            task = self.open_task("Short flow", "Close on a declared check", "--check", check, "--start")
            result = self.hipson("task", "close", "--task", task)
            require(result.returncode == 0, output_of(result))
        self.check("task do (open, worker, check, close)", "task", "do", "--title", "One shot", "--check", check,
                   "--actor", "smoke", "--scope", "src", "--", PYTHON, "-c", 'print("one-shot worker")')

    def scripted_flow(self) -> None:
        worker = str(ROOT / "e2e/agents/bin/flow-worker")
        spec = self.work / "flow.json"
        spec.write_text(json.dumps({
            "version": 1,
            "title": "Fix pair parser",
            "objective": "Split pairs and trim whitespace",
            "project": str(self.project),
            "checks": [[PYTHON, "acceptance.py"]],
            "builder": {"actor": "builder", "scope": ["src/parser"], "command": [PYTHON, worker, "builder", "correct"]},
            "reviewer": {"actor": "reviewer", "command": [PYTHON, worker, "reviewer", "strict"]},
        }), encoding="utf-8")
        result = self.hipson("task", "flow", "--spec", spec, "--json", timeout=180)
        with self.report.checking("task flow reaches accepted"):
            flow = command_json(result)
            require(flow["state"] == "accepted", output_of(result))
        with self.report.checking("task flow records review with source=snapshot"):
            flow = command_json(result)
            events = command_json(self.hipson("task", "events", "--task", flow["task"], "--json"))
            reviewed = [event["summary"] for event in events if event["type"] == "reviewed"]
            require(len(reviewed) == 2 and all(entry.endswith("source=snapshot") for entry in reviewed), repr(reviewed))

    def mcp(self) -> None:
        requests = [
            {"id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"},
            }},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
            {"id": 3, "method": "tools/call", "params": {"name": "task.board", "arguments": {}}},
        ]
        wire = "".join(json.dumps({"jsonrpc": "2.0", **request}) + "\n" for request in requests)
        with self.report.checking("MCP stdio initialize, tools/list, task.board"):
            result = self.hipson("mcp", "serve", "--project", ".", "--stdio", stdin=wire)
            require(result.returncode == 0, output_of(result))
            replies = {reply["id"]: reply for reply in map(json.loads, result.stdout.splitlines())}
            require(set(replies) == {1, 2, 3} and all("result" in reply for reply in replies.values()),
                    output_of(result))
            require(replies[1]["result"]["serverInfo"]["name"] == "hipson"
                    and "task.brief" in {tool["name"] for tool in replies[2]["result"]["tools"]}
                    and not replies[3]["result"].get("isError") and replies[3]["result"]["content"], output_of(result))

    def runtime_diagnostics(self) -> None:
        with self.report.checking("hipson agent runtimes --json"):
            # Keep the inventory offline even on a workstation with provider tools installed.
            result = self.hipson("agent", "runtimes", "--json", env={**self.env, "PATH": str(self.work)})
            inventory = command_json(result)
            require(isinstance(inventory["runtimes"], dict), output_of(result))

    def suites(self, python: str) -> None:
        # The existing suite includes checks that inspect the source checkout from its cwd.
        # Tests that apply init use persistent config locks, so give them a disposable home.
        home = self.work / "suite-home"
        home.mkdir()
        suite_env = {**environment(self.work), "HIPSON_BIN": self.cli, "HOME": str(home), "USERPROFILE": str(home)}
        for name, args in (
            ("scripts/run_tests.py", ("scripts/run_tests.py",)),
            ("pytest", ("-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs")),
        ):
            name = f"test suite: {name}"
            try:
                result = self.run(python, *args, cwd=ROOT, env=suite_env, timeout=1200)
            except (OSError, subprocess.SubprocessError) as exc:
                self.report.failure(name, str(exc))
                continue
            stdout = result.stdout.splitlines()
            lines = output_of(result).splitlines()
            if result.returncode == 0:
                self.report.success(name)
            else:
                errors = [line for line in lines if line.startswith(("FAIL ", "FAIL:", "FAILED "))]
                self.report.failure(f"{name} (exit {result.returncode})", "\n".join(errors[:15] or lines))
            if stdout:
                self.report.log(f"      {stdout[-1]}")
            skips = Counter(line for line in lines if line.startswith("SKIP") or "SKIPPED" in line)
            for line, count in sorted(skips.items())[:10]:
                self.report.log(f"      {count} {line}", terminal=False)


def hard_kill(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    with suppress(ProcessLookupError):
        if sys.platform == "win32":
            child.kill()  # The runner's Job Object terminates the worker tree.
        elif os.getpgid(child.pid) == child.pid:
            os.kill(-child.pid, signal.SIGKILL)  # task run owns this POSIX process group.
        else:
            os.kill(child.pid, signal.SIGKILL)  # The runner has not created its session yet.
    child.wait(timeout=10)


def venv_path(directory: Path, executable: str) -> Path:
    return directory / (f"Scripts/{executable}.exe" if sys.platform == "win32" else f"bin/{executable}")


def sync_checkout(report: Report, uv: str | None) -> bool:
    if uv is None:
        report.failure("uv is installed", "Install uv from https://docs.astral.sh/uv/getting-started/installation/")
        return False
    with report.checking("uv sync --all-extras (install from this checkout)"):
        result = subprocess.run([uv, "sync", "-q", "--all-extras"], cwd=ROOT, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=600)
        require(result.returncode == 0, f"exit {result.returncode}\n{output_of(result)}")
        return True
    return False


def run_smoke(args: argparse.Namespace, work: Path, report: Report) -> None:
    uv = shutil.which("uv")
    report.log(f"Hipson platform smoke test - {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}")
    report.log(f"system:  {platform.system()} {platform.release()} {platform.machine()}")
    if platform.system() == "Darwin":
        report.log(f"macOS:   {platform.mac_ver()[0]}")
    if bash := os.environ.get("HIPSON_SMOKE_BASH_VERSION"):
        report.log(f"bash:    {bash}")
    for label, cmd, missing in (
        ("git rev:", ("git", "-C", ROOT, "rev-parse", "--short", "HEAD"), "unknown"),
        ("branch: ", ("git", "-C", ROOT, "rev-parse", "--abbrev-ref", "HEAD"), "unknown"),
        ("uv:     ", (uv or "uv", "--version"), "missing"),
    ):
        try:
            result = subprocess.run(cmd, cwd=work, capture_output=True, encoding="utf-8", errors="replace", timeout=10)
            version = result.stdout.strip() if result.returncode == 0 else missing
        except (OSError, subprocess.SubprocessError):
            version = missing
        report.log(f"{label} {version}")
    report.log(f"python:  {platform.python_version()} sqlite {sqlite3.sqlite_version}")
    report.log()

    if args.skip_sync:
        report.log("uv sync skipped (--skip-sync)")
    elif not sync_checkout(report, uv):
        return
    venv = ROOT / ".venv"
    candidate = str(Path(args.hipson).expanduser().absolute()) if args.hipson else (
        shutil.which("hipson") or str(venv_path(venv, "hipson"))
    )
    cli = shutil.which(candidate)
    if cli is None:
        report.failure("hipson CLI is installed", f"Executable not found: {candidate}")
        return
    smoke = Smoke(str(Path(cli).absolute()), work, report)
    smoke.check("hipson --help", "--help", cwd=work)
    smoke.check("hipson doctor", "doctor", cwd=work)
    smoke.create_project()
    smoke.check("hipson init --dry-run (no client config edits)", "init", "--project", ".", "--client", "none",
                "--dry-run", "--json")
    smoke.manual_lifecycle()
    smoke.check("hipson task run --help", "task", "run", "--help")
    runner = smoke.runner_command()
    smoke.worker_lifecycle(runner, crash=True)
    smoke.worker_lifecycle(runner, crash=False)
    smoke.short_flow()
    smoke.scripted_flow()
    smoke.mcp()
    smoke.runtime_diagnostics()
    if not os.environ.get("SMOKE_SKIP_SUITES"):
        suite_python = venv_path(venv, "python")
        smoke.suites(str(suite_python) if not args.skip_sync and suite_python.is_file() else PYTHON)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hipson", metavar="PATH", help="CLI under test (default: PATH, then the checkout .venv)")
    parser.add_argument("--skip-sync", action="store_true", help="Use an already installed CLI without running uv sync")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    final_report = ROOT / "platform-smoke-report.txt"
    with tempfile.TemporaryDirectory(prefix="hipson smoke ü 日本 ") as temp:
        work = Path(temp).resolve()
        report_path = work / "report.txt"
        with report_path.open("w", encoding="utf-8") as stream:
            report = Report(stream, sys.stdout)
            try:
                run_smoke(args, work, report)
            except (SmokeError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
                report.failure("platform smoke could not continue", str(exc))
            status = report.finish()
        # Publish only after verification and review have finished.
        shutil.copyfile(report_path, final_report)
    print(f"Report: {final_report}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
