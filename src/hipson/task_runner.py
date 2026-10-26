"""Lead-controlled, provider-free worker runner for the task ledger."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

from hipson import ledger
from hipson.redaction import redact_text
from hipson.task_posix import ProcessTree
from hipson.task_windows import Job


class RunParser(argparse.ArgumentParser):
    def parse_known_args(self, args=None, namespace=None):
        parsed, unknown = super().parse_known_args(args, namespace)
        if self.prog == "hipson task run" and unknown:
            self.error(f"unrecognized arguments: {' '.join(unknown)}")
        return parsed, unknown

    def error(self, message: str) -> NoReturn:
        if self.prog != "hipson task run":
            super().error(message)
        self.print_usage(sys.stderr)
        self.exit(64, f"hipson task run: {message}\n")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=run)
    parser.add_argument("--task", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--role", choices=ledger.ROLES, required=True)
    parser.add_argument("--mode", choices=ledger.MODES, default="ro")
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--lease-seconds", type=int)
    parser.add_argument("--ref", action="append", default=[])
    parser.add_argument("--result-file")
    parser.add_argument("--rollout", choices=["codex"])
    parser.add_argument("--allow-project-mismatch", action="store_true")
    parser.add_argument("worker_command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")


def _message(text: str) -> None:
    print(f"hipson task run: {redact_text(text)}", file=sys.stderr, flush=True)


def _project_check(project: str) -> dict[str, object]:
    expected, cwd = Path(project).resolve(), Path.cwd().resolve()
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    try:
        git = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, env=env,
                             capture_output=True, text=True, encoding="utf-8", check=False, timeout=10)
    except FileNotFoundError:
        git = None
    is_git = git is not None and git.returncode == 0
    actual = Path(git.stdout.strip()).resolve() if is_git and git is not None else cwd
    matches = (expected == actual and cwd.is_relative_to(actual)) or (not is_git and cwd.is_relative_to(expected))
    return {"expected": str(expected), "actual": str(actual), "matches": matches}


def _rollout_facts(nonce: str, started: float, refs: list[str]) -> str:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for path in (home / "sessions").rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < started:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if nonce not in content:
            continue
        model = re.search(r'"model"\s*:\s*"([^"\n]*)"', content)
        effort = re.search(r'"reasoning_effort"\s*:\s*"([^"\n]*)"', content)
        refs.append(str(path))
        return f" model={model[1] if model else '?'} effort={effort[1] if effort else '?'}"
    return " model=? effort=? (rollout not found)"


def _finish(db: Path, args: argparse.Namespace, assignment: str, code: int, signum: int,
            note: str, nonce: str, started: float) -> None:
    refs = list(args.ref)
    try:
        facts = _rollout_facts(nonce, started, refs) if args.rollout else ""
    except OSError:
        facts = " model=? effort=? (rollout not found)"
    name = signal.Signals(signum).name.removeprefix("SIG") if signum else str(code)
    result = "cancelled" if signum else "passed" if code == 0 else "failed"
    summary = f"worker process exit={name}{facts}{note}; process result only, not lead verification"
    report = None
    if args.result_file:
        try:
            path = Path(args.result_file).expanduser()
            if path.exists() and path.stat().st_size:
                report = ledger.load_task_result(path)
        except Exception as exc:  # Evidence must never override the process result, even on parser/resource limits.
            _message(f"result file rejected ({type(exc).__name__})")
            summary += "; result file rejected"
    try:
        with closing(ledger.Ledger(db)) as store:
            try:
                store.finish(assignment, result=result, summary=summary, refs=refs, worker_result=report)
            except Exception:
                if report is None:
                    raise
                _message("result file rejected; recording process outcome without worker evidence")
                store.finish(assignment, result=result, summary=summary + "; result file rejected", refs=refs)
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        _message(f"could not finish {assignment}: {exc}; run 'hipson task reap'")


def run(args: argparse.Namespace) -> int:
    command = args.worker_command
    try:
        if not command or command[0] != "--" or len(command) == 1:
            raise ValueError("a command after -- is required")
        ledger.require_text(args.task, "task")
        ledger.require_text(args.actor, "actor")
        ledger.validate_assignment_scope(args.scope, mode=args.mode)
        if args.lease_seconds is not None:
            if args.lease_seconds < 0:
                raise ValueError("--lease-seconds must be nonnegative")
            datetime.now(UTC) + timedelta(seconds=args.lease_seconds)
        grace = float(os.environ.get("AGENT_RUN_GRACE_SECONDS", "15"))
        if not math.isfinite(grace) or grace < 0:
            raise ValueError("AGENT_RUN_GRACE_SECONDS must be finite and nonnegative")
    except (ValueError, OverflowError, ledger.LedgerError) as exc:
        _message(str(exc))
        return 64
    db = Path(args.db).expanduser().resolve() if args.db else ledger.default_ledger_db()
    try:
        with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
            project = store.show(args.task)["project"]
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        _message(f"could not read task {args.task}: {exc}; worker not started")
        return 75
    try:
        check = _project_check(project)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _message(f"could not verify task project: {exc}; worker not started")
        return 76
    mismatch = not check["matches"]
    if mismatch and not args.allow_project_mismatch:
        _message(f"project mismatch {json.dumps(check)}; worker not started (explicit override: --allow-project-mismatch)")
        return 76
    return _execute(db, args, command[1:], mismatch, check, grace)


def _execute(db: Path, args: argparse.Namespace, command: list[str], mismatch: bool,
             check: dict[str, object], grace: float) -> int:
    if sys.platform != "win32":
        if os.getpgrp() == os.getpid():
            # A group leader cannot setsid; keep it as a signal/exit proxy and let
            # a new owner establish a private session before taking any claims.
            signals = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
            previous = {}
            try:
                owner = os.fork()
                if owner == 0:
                    try:
                        os.setsid()
                        code = _execute_owned(db, args, command, mismatch, check, grace, mask)
                    except BaseException as exc:
                        _message(f"private supervisor failed: {exc}; run 'hipson task reap'")
                        code = 75
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os._exit(code)
                def forward(number: int, frame: object) -> None:
                    try:
                        os.kill(owner, number)
                    except ProcessLookupError:
                        pass
                previous = {number: signal.signal(number, forward) for number in signals}
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                _, status = os.waitpid(owner, 0)
                outcome = os.waitstatus_to_exitcode(status)
                return outcome if outcome >= 0 else 128 - outcome
            finally:
                for number, handler in previous.items():
                    signal.signal(number, handler)
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        os.setsid()
    return _execute_owned(db, args, command, mismatch, check, grace)


def _execute_owned(db: Path, args: argparse.Namespace, command: list[str], mismatch: bool,
                   check: dict[str, object], grace: float, mask: set[int | signal.Signals] | None = None) -> int:
    windows = sys.platform == "win32"
    signum = 0

    def interrupted(number: int, frame: object) -> None:
        nonlocal signum
        if not signum:
            signum = number

    signals = [signal.SIGINT, signal.SIGTERM]
    signals.append(signal.SIGBREAK if windows else signal.SIGHUP)  # type: ignore[attr-defined]
    previous = {number: signal.signal(number, interrupted) for number in signals}
    if sys.platform != "win32" and mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    child: subprocess.Popen[bytes] | None = None
    job: Job | None = None
    tree: ProcessTree | None = None
    assignment = ""
    launching = False
    code, note, safe = 75, "", True
    nonce, started = f"ledger-run-{uuid.uuid4()}", time.time()
    try:
        if not windows:
            tree = ProcessTree()
        with closing(ledger.Ledger(db)) as store:
            assignment = store.assign(args.task, actor=args.actor, role=args.role, mode=args.mode, scope=args.scope,
                                      owner_pid=os.getpid(), owner_host=socket.gethostname(),
                                      owner_boot_id=ledger._boot_id(), owner_pid_namespace=ledger._pid_namespace(),
                                      lease_seconds=args.lease_seconds)["id"]
            if mismatch:
                store.add_event(args.task, actor=args.actor, type="project_mismatch_override",
                                summary=f"Explicit --allow-project-mismatch: {json.dumps(check)}", refs=[assignment])
        _message(f"{args.task} {assignment} {args.actor}/{args.role}/{args.mode}")
        env = {**os.environ, "HIPSON_ROLE": "worker", "HIPSON_TASK": args.task, "HIPSON_ASSIGNMENT": assignment,
               "HIPSON_RUN_OWNER": str(os.getpid()), "HIPSON_RUN_DB": str(db)}
        with tempfile.TemporaryFile() as brief:
            if args.rollout:
                # Reading stdin in a daemon keeps cancellation responsive even with an open pipe.
                ready = threading.Event()
                errors: list[Exception] = []
                def read_brief() -> None:
                    try:
                        while chunk := os.read(sys.stdin.fileno(), 65536):
                            brief.write(chunk)
                        brief.write(f"\n\n<!-- {nonce} -->\n".encode())
                        brief.seek(0)
                    except (OSError, ValueError) as exc:
                        errors.append(exc)
                    finally:
                        ready.set()
                threading.Thread(target=read_brief, daemon=True).start()
                while not ready.wait(0.05) and not signum:
                    pass
                if errors and not signum:
                    raise OSError(f"could not read worker brief: {errors[0]}")
            if not signum:
                if windows:
                    job = Job()
                    job.protect_runner()
                launching = True
                child = subprocess.Popen(command, env=env, stdin=brief if args.rollout else None,
                                         creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | 4) if windows else 0)  # type: ignore[attr-defined]  # CREATE_SUSPENDED
                launching = False
                safe = False
                if tree:
                    tree.child = child
                if job:
                    try:
                        job.attach(child)
                    except OSError:
                        child.kill()
                        child.wait()
                        raise
                while child.poll() is None and not signum:
                    if tree:
                        tree.members()
                    time.sleep(0.05)
                outcome = child.poll()
                code = outcome if outcome is not None else 0
                if code < 0:
                    code = 128 - code  # Shell-compatible status for a child killed by a signal.
                if signum or _active(job, tree):
                    if not signum:
                        note = "; leftover processes stopped"
                        _message("worker left processes running; stopping them before finishing")
                    safe = _stop(child, job, signum or signal.SIGTERM, grace, tree)
                else:
                    safe = True
            if signum:
                code = 128 + signum
    except (ledger.LedgerError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        _message(f"{exc}; worker not started" if child is None else str(exc))
        if child is not None:
            try:
                safe = _stop(child, job, signal.SIGTERM, grace, tree)
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                safe = False
                _message(f"could not confirm worker cleanup: {cleanup_error}")
        code = (127 if isinstance(exc, FileNotFoundError) else 126) if launching else 75
    finally:
        if job:
            job.close()
        if child and (safe or job or child.poll() is not None):
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                safe = False
        if signum:
            code = 128 + signum
        if assignment and safe:
            _finish(db, args, assignment, code, signum, note, nonce, started)
        elif assignment:
            _message(f"worker processes survived KILL; leaving {assignment} active (run 'hipson task reap' later)")
        for number, handler in previous.items():
            signal.signal(number, handler)
        if tree:
            tree.close()
    return code


def _active(job: Job | None, tree: ProcessTree | None = None) -> bool:
    if job:
        return job.active()
    if tree is None:
        raise OSError("worker process tracking is unavailable")
    return tree.active()


def _stop(child: subprocess.Popen[bytes], job: Job | None, number: int, grace: float,
          tree: ProcessTree | None = None) -> bool:
    if job:
        try:
            job.interrupt(child.pid)
        except OSError as exc:
            # Console-less workers cannot receive a break. Still enforce the grace
            # period and Job cleanup before releasing their claims.
            _message(f"could not forward Ctrl+Break: {exc}; enforcing Job cleanup after grace")
    elif sys.platform != "win32":  # job is None only off Windows; mypy needs the platform check
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        if tree is None:
            raise OSError("worker process tracking is unavailable")
        tree.send(number)  # Includes group members and detached descendants; excludes the owner.
    deadline = time.monotonic() + grace
    while _active(job, tree) and time.monotonic() < deadline:
        child.poll()
        time.sleep(0.05)
    if _active(job, tree):
        if job:
            job.terminate()
        elif sys.platform != "win32" and tree:
            tree.send(signal.SIGKILL)
    deadline = time.monotonic() + 5
    while _active(job, tree) and time.monotonic() < deadline:
        child.poll()
        if sys.platform != "win32" and tree:
            tree.send(signal.SIGKILL)  # Also catch children spawned during escalation.
        time.sleep(0.05)
    return not _active(job, tree)
