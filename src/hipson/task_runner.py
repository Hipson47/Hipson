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
from typing import Any, NoReturn

from hipson import ledger, task_activity, task_observation, worker_env
from hipson.redaction import redact_text
from hipson.task_posix import ProcessTree
from hipson.task_windows import Job, batch_command

REMAINDER_COMMANDS = ("hipson task run", "hipson task verify", "hipson task close", "hipson task do")
GIT_LOCATION_VARIABLES = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_ATTR_SOURCE", "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS",
})


def git_redirecting_variable(key: str) -> bool:
    """Variables that select a repository or inject configuration (GIT_CONFIG*, GIT_CONFIG_KEY_n, ...)."""
    name = key.upper()
    return name in GIT_LOCATION_VARIABLES or name.startswith("GIT_CONFIG")


def supervise(command: list[str], *, cwd: str | None = None, capture: bool = False,
              timeout: float | None = None, contain: bool = False) -> tuple[int, bytes, bytes]:
    """Run a Hipson supervisor (task run / task verify) and let it clean up on cancellation.

    On POSIX the supervisor leads its own session, so terminal signals reach only this caller: forward
    them instead of letting Python kill the supervisor and orphan its tree. On Windows it shares the
    console and receives Ctrl+C itself, so keep waiting for its cleanup. A timeout asks the supervisor
    to stop (SIGTERM / terminate), allows a grace period, then kills it and raises TimeoutExpired.
    Raw commands opt into a dedicated supervisor so descendant tracking never includes the caller's
    other children. Existing task supervisors already provide their own containment.
    """
    if contain:
        # -P: the command runs in the project directory, whose modules must not shadow the stdlib.
        command = [sys.executable, '-P', '-c',
                   'import sys; from hipson.task_runner import supervise_command; '
                   'sys.exit(supervise_command(sys.argv[1:]))', *command]
    pipe = subprocess.PIPE if capture else None
    child = subprocess.Popen(command, cwd=cwd, stdout=pipe, stderr=pipe)

    def forward(number: int, frame: object) -> None:
        if child.poll() is None:
            try:
                child.send_signal(number)
            except OSError:
                pass

    previous: dict[int, Any] = {}
    if sys.platform != 'win32' and threading.current_thread() is threading.main_thread():
        previous = {number: signal.signal(number, forward) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    deadline = None if timeout is None else time.monotonic() + timeout
    stopping = False
    try:
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                output, errors = child.communicate(timeout=remaining)
                if stopping:
                    raise subprocess.TimeoutExpired(command, timeout or 0, output, errors)
                return child.returncode, output or b'', errors or b''
            except KeyboardInterrupt:
                continue
            except subprocess.TimeoutExpired:
                if stopping:
                    child.kill()
                    child.communicate()
                    raise
                stopping = True
                child.terminate()  # SIGTERM lets a POSIX supervisor run its own cleanup.
                deadline = time.monotonic() + 30
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def supervise_command(command: list[str]) -> int:
    """Internal raw-command supervisor; isolate process ownership from the calling interpreter."""
    windows = sys.platform == 'win32'
    signals: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)
    if sys.platform != 'win32':  # Spelled out so type checkers narrow the POSIX-only names.
        os.setsid()
        signals += (signal.SIGHUP,)
    signum = 0

    def interrupted(number: int, frame: object) -> None:
        nonlocal signum
        signum = number

    previous = {number: signal.signal(number, interrupted) for number in signals}
    tree: ProcessTree | None = None
    job: Job | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        if windows:
            job = Job()
            job.protect_runner()
        else:
            tree = ProcessTree()
        if sys.platform == 'win32':
            launch, env = batch_command(command, dict(os.environ), 'HIPSON_VERIFY_ARG_')
            child = subprocess.Popen(launch, env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 4)
        else:
            # Its own process group lets the fallback stop the command's tree without stopping this supervisor.
            child = subprocess.Popen(command, env=dict(os.environ), process_group=0)
        if tree:
            tree.child = child
        if job:
            job.attach(child)
        while child.poll() is None and not signum:
            if tree:
                tree.members()
            time.sleep(0.05)
        return 128 + signum if signum else int(child.returncode or 0)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 127
    finally:
        try:
            if child is not None:
                try:
                    stopped = _stop(child, job, signal.SIGTERM, 1, tree)
                except Exception:  # Tree inspection itself failed (e.g. ps missing): fall back below.
                    stopped = False
                if not stopped:
                    _stop_without_tracking(child)
                    raise OSError('could not confirm verification process cleanup')
                child.wait(timeout=5)
        finally:
            if job:
                job.close()
            if tree:
                tree.close()
            for number, handler in previous.items():
                signal.signal(number, handler)


def _stop_without_tracking(child: subprocess.Popen[bytes]) -> None:
    """Last resort when descendants cannot be enumerated: stop the command's own process group."""
    if sys.platform != 'win32':
        # The command leads its process group: this reaches its non-detached descendants, never the supervisor.
        for number, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 0.0)):
            try:
                os.killpg(child.pid, number)
            except OSError:
                pass
            time.sleep(grace)
    child.kill()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


class RunParser(argparse.ArgumentParser):
    def parse_known_args(self, args=None, namespace=None):
        parsed, unknown = super().parse_known_args(args, namespace)
        if self.prog in REMAINDER_COMMANDS and unknown:
            self.error(f"unrecognized arguments: {' '.join(unknown)}")
        return parsed, unknown

    def error(self, message: str) -> NoReturn:
        if self.prog not in REMAINDER_COMMANDS:
            super().error(message)
        self.print_usage(sys.stderr)
        self.exit(64, f"{self.prog}: {message}\n")


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
    add_observe_arguments(parser)
    add_env_arguments(parser)
    parser.add_argument("worker_command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")


def add_env_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", choices=worker_env.MODES,
                        help="Worker environment: inherit the lead's (default) or minimal, an allowlist of login, "
                             "locale, proxy, certificate and agent-home variables plus HIPSON_* "
                             "(also set by worker_env in .hipson/policy.json)")
    parser.add_argument("--env-allow", action="append", default=[], metavar="NAME",
                        help="With --env minimal, also pass this variable (repeatable), e.g. OPENAI_API_KEY")


MENTIONS_WORKER_ENV = re.compile(r"worker[\s_\-]*env|\\[uU]", re.IGNORECASE)


def worker_environment(args: argparse.Namespace, project: str) -> tuple[str, list[str]]:
    """The requested worker environment mode and extra allowed names; ValueError when they cannot apply.

    Only .hipson/policy.json, read by the exact JSON parser, configures the worker environment. Any doubt about
    a policy that may ask for minimal refuses the run without --env instead of inheriting everything.
    """
    from hipson.policy import POLICY_JSON, POLICY_YAML, load_policy
    explicit = getattr(args, "env", None) is not None
    root = Path(project)
    path = root / POLICY_JSON if (root / POLICY_JSON).exists() else root / POLICY_YAML  # As load_policy chooses.
    try:
        text: str | None = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return worker_env.resolve(args, {"valid": True, "policy": {}})
    except OSError:
        text = None  # Unreadable: it may set worker_env.
    # Spellings, padding, case and \u escapes all count: a policy that may mean worker_env is never ignored.
    mentions = text is None or MENTIONS_WORKER_ENV.search(text) is not None
    if path.name != POLICY_JSON.name:
        if mentions and not explicit:
            raise ValueError("worker_env is read only from .hipson/policy.json, and the policy.yaml in effect may "
                             "set it; move the setting to policy.json or pass --env explicitly")
        return worker_env.resolve(args, {"valid": True, "policy": {}})
    try:
        payload = load_policy(project)
    except (SystemExit, OSError, ValueError) as exc:
        if mentions and not explicit:
            raise ValueError(f"project policy cannot be read ({type(exc).__name__}) and may set worker_env; "
                             "fix .hipson/policy.json or pass --env explicitly") from None
        payload = {}  # A broken policy that never mentions it keeps the pre-1.7 behaviour.
    return worker_env.resolve(args, payload)


def add_observe_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--observe", action="store_true",
                        help="Snapshot the project before the run and record what changed against --scope/--mode "
                             "(also enabled by observe_changes in .hipson/policy.json)")
    parser.add_argument("--observe-max-files", type=int, help="Observation file cap (default 20000)")
    parser.add_argument("--observe-max-bytes", type=int, help="Observation content-hash budget in bytes (default 256 MiB)")


def observation_caps(args: argparse.Namespace, project: str) -> task_observation.Caps | None:
    """Opt-in: --observe, or observe_changes in a valid project policy. None means no observation."""
    from hipson.policy import load_policy
    try:
        payload = load_policy(project)
    except (SystemExit, OSError, ValueError):
        payload = {}
    # Policy caps apply whether the policy or --observe enables observation; an invalid policy enables nothing.
    policy: dict[str, Any] = payload.get("policy", {}) if payload.get("valid") else {}
    if not (getattr(args, "observe", False) or policy.get("observe_changes") is True):
        return None
    files = getattr(args, "observe_max_files", None) or policy.get("observe_max_files") or task_observation.MAX_FILES
    budget = getattr(args, "observe_max_bytes", None) or policy.get("observe_max_bytes") or task_observation.MAX_HASH_BYTES
    return task_observation.Caps(max_files=files, max_hash_bytes=budget)


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
            note: str, nonce: str, started: float, snapshot: dict, project: str,
            child_signal: int = 0) -> None:
    refs = list(args.ref)
    try:
        facts = _rollout_facts(nonce, started, refs) if args.rollout else ""
    except OSError:
        facts = " model=? effort=? (rollout not found)"
    name = signal.Signals(signum).name.removeprefix("SIG") if signum else str(code)
    result = "cancelled" if signum else "passed" if code == 0 else "failed"
    summary = f"worker process exit={name}{facts}{note}; process result only, not lead verification"
    report = None
    result_state = 'none'
    if args.result_file:
        try:
            path = Path(args.result_file).expanduser()
            if path.exists() and path.stat().st_size:
                report = ledger.load_task_result(path)
                result_state = 'valid'
        except Exception as exc:  # Evidence must never override the process result, even on parser/resource limits.
            _message(f"result file rejected ({type(exc).__name__})")
            summary += "; result file rejected"
            result_state = 'rejected'
    try:
        # The final snapshot is taken while this assignment still owns its claims, so writers that start after
        # it ends cannot leak into what is attributed to it.
        after = task_observation.capture_after(db, assignment)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _message(f"could not take the final observation snapshot: {type(exc).__name__}")
        after = None
    journal = {
        **task_activity.project_changes(project), 'result_state': result_state,
        'exit_code': code, 'signal': signum or child_signal or None,
        'duration_seconds': max(0, time.time() - started),
        'log_tail': snapshot.get('observations', []), 'reason': summary,
    }
    try:
        with closing(ledger.Ledger(db)) as store:
            try:
                store.finish(assignment, result=result, summary=summary, refs=refs, worker_result=report,
                             failure_journal=journal)
            except Exception:
                if report is None:
                    raise
                _message("result file rejected; recording process outcome without worker evidence")
                journal['result_state'] = 'rejected'
                store.finish(assignment, result=result, summary=summary + "; result file rejected", refs=refs,
                             failure_journal=journal)
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        _message(f"could not finish {assignment}: {exc}; run 'hipson task reap'")
        return
    try:
        observed = task_observation.complete(db, assignment, after=after)
        if observed is None and getattr(args, "observation_error", None):
            with closing(ledger.Ledger(db)) as store:
                observed = store.record_changes(assignment, task_observation.unobserved(args.mode, args.observation_error))
    except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
        _message(f"could not record observed changes for {assignment}: {exc}; run 'hipson task reap'")
        return
    if observed is not None:
        _message(f"{assignment} observed changes: {task_observation.summary(observed['changes'])}")


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
        for name in ("observe_max_files", "observe_max_bytes"):
            value = getattr(args, name, None)
            if value is not None and value < 1:
                raise ValueError(f"--{name.replace('_', '-')} must be a positive integer")
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
    try:
        args.worker_env = worker_environment(args, str(check["expected"]))
    except ValueError as exc:
        _message(f"{exc}; worker not started")
        return 64
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
    verifying = hasattr(args, 'verification_id')
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
    activity: task_activity.Activity | None = None
    progress_dir: tempfile.TemporaryDirectory[str] | None = None
    progress = None
    child_signal = 0
    snapshot: dict = {}
    next_heartbeat = 0.0
    verify_started = time.monotonic()
    verify_stamp = ledger._timestamp()
    verify_before: dict = {}
    worker_base: dict[str, str] = {}
    builder_seq = 0
    try:
        if not windows:
            tree = ProcessTree()
        with closing(ledger.Ledger(db)) as store:
            if verifying:
                from hipson.task_verification import project_state
                builder_seq = store._builder_finished_seq(args.task)
                verify_before = project_state(str(check['expected']), db)
            else:
                env_mode, env_allow = getattr(args, "worker_env", (worker_env.DEFAULT_MODE, []))
                inherited = {key: value for key, value in os.environ.items() if not git_redirecting_variable(key)}
                worker_base, env_record = worker_env.select(inherited, env_mode, env_allow)
                assignment = store.assign(args.task, actor=args.actor, role=args.role, mode=args.mode, scope=args.scope,
                                      owner_pid=os.getpid(), owner_host=socket.gethostname(),
                                      owner_boot_id=ledger._boot_id(), owner_pid_namespace=ledger._pid_namespace(),
                                      lease_seconds=args.lease_seconds, environment=env_record)["id"]
            if mismatch:
                store.add_event(args.task, actor=args.actor, type="project_mismatch_override",
                                summary=f"Explicit --allow-project-mismatch: {json.dumps(check)}", refs=[assignment])
        if not verifying:
            _message(f"{args.task} {assignment} {args.actor}/{args.role}/{args.mode}")
            caps = observation_caps(args, str(check['expected']))
            if caps is not None:
                # Before launch and after the claim: the baseline is what this worker starts from.
                try:
                    task_observation.start(db, assignment, str(check['expected']), caps)
                except (OSError, ValueError) as exc:
                    # Requested but impossible: never launch unobserved, and record why instead of nothing.
                    reason = f"baseline failed: {type(exc).__name__}"
                    try:
                        task_observation.start_failed(db, assignment, reason)
                    except OSError:
                        args.observation_error = reason  # Storage itself fails: _finish records it directly.
                    raise OSError(f"could not take the observation baseline: {exc}") from exc
            # assign() may have recovered orphans; finish their observations now rather than at the next reap.
            task_observation.complete_pending(db)
        progress_dir = tempfile.TemporaryDirectory(prefix='hipson-progress-')
        progress_path = Path(progress_dir.name) / 'progress'
        progress = progress_path.open('w+b', buffering=0)
        activity = task_activity.Activity(progress)
        snapshot = activity.snapshot()
        if not verifying:
            with closing(ledger.Ledger(db)) as store:
                store.observe(assignment, snapshot)
        # Checks and workers see the same repository as the fingerprint: no inherited variable may select
        # another repository, index or object store, or inject configuration. Identity (GIT_AUTHOR_*) and
        # transport (GIT_SSH_COMMAND) settings stay, so checks that commit fixtures or fetch still work.
        # Lead checks always inherit: they are the lead's own declared commands and need its toolchain. Workers
        # get the environment selected at assignment, which --env minimal narrows to an allowlist.
        inherited = {key: value for key, value in os.environ.items() if not git_redirecting_variable(key)}
        env = inherited if verifying else {**worker_base, "HIPSON_ROLE": "worker", "HIPSON_TASK": args.task, "HIPSON_ASSIGNMENT": assignment,
               "HIPSON_RUN_OWNER": str(os.getpid()), "HIPSON_RUN_DB": str(db),
               'HIPSON_PROGRESS_FILE': str(progress_path)}
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
                launch: str | list[str] = command
                if windows:
                    try:  # .cmd/.bat targets (npm, npx, claude) would otherwise reparse argument text.
                        launch, env = batch_command(command, env, 'HIPSON_RUN_ARG_')
                    except ValueError as exc:
                        raise OSError(str(exc)) from exc
                child = subprocess.Popen(launch, env=env, stdin=brief if args.rollout else None,
                                         cwd=str(check['expected']) if verifying else None,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
                if child.stdout is None or child.stderr is None:
                    raise OSError('worker output pipes are unavailable')
                activity.relay(child.stdout, 'stdout', None if verifying else sys.stdout.fileno())
                activity.relay(child.stderr, 'stderr', None if verifying else sys.stderr.fileno())
                while child.poll() is None and not signum:
                    if verifying and time.monotonic() - verify_started >= args.timeout:
                        note = '; verification timed out'
                        break
                    if tree:
                        tree.members()
                    if not verifying and time.monotonic() >= next_heartbeat:
                        snapshot = activity.snapshot()
                        with closing(ledger.Ledger(db)) as store:
                            store.observe(assignment, snapshot)
                        next_heartbeat = time.monotonic() + task_activity.HEARTBEAT_SECONDS
                    time.sleep(0.05)
                outcome = child.poll()
                code = outcome if outcome is not None else 124 if verifying else 0
                if code < 0:
                    child_signal = -code
                    code = 128 - code  # Shell-compatible status for a child killed by a signal.
                if signum or outcome is None or _active(job, tree):
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
        if activity:
            activity.join()
            try:
                snapshot = activity.snapshot(final=True)
                if not verifying:
                    with closing(ledger.Ledger(db)) as store:
                        store.observe(assignment, snapshot)
            except (ledger.LedgerError, OSError, sqlite3.Error) as exc:
                _message(f'could not record final activity: {exc}')
        if verifying and verify_before:
            from hipson.task_verification import finish
            finish(db, args, code, verify_stamp, verify_started, verify_before, builder_seq,
                   snapshot, str(check['expected']), safe)
        elif assignment and safe:
            _finish(db, args, assignment, code, signum, note, nonce, started, snapshot,
                    str(check['expected']), child_signal)
        elif assignment:
            _message(f"worker processes survived KILL; leaving {assignment} active (run 'hipson task reap' later)")
        if progress:
            progress.close()
        if progress_dir:
            progress_dir.cleanup()
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
