#!/usr/bin/env python3
"""Local, opt-in agent missions with observable-state oracles (no provider SDK)."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hipson.ledger import Ledger
from hipson.redaction import REDACTION, redact_text, redact_value

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # Scenario plugins share repo-local helpers when run as a script.
SCENARIOS = ROOT / "e2e" / "agents" / "scenarios"
ISOLATION_FLAGS = ("--ephemeral", "--ignore-user-config", "--ignore-rules")
AUTH_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")


def scrub_text(text: str) -> str:
    # Match known credential values too, including nonstandard token formats and
    # bare diagnostic output with no key name. Never include these values in logs.
    secrets = {value for key, value in os.environ.items() if value and re.search(
        r"(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|ACCESS_KEY_ID)(?:_|$)", key, re.I)}
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, REDACTION)
    return redact_text(text)


def scrub_value(value: Any) -> Any:
    value = redact_value(value)
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    return value


def load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("agent_e2e_plugin", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load scenario module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(scrub_value(data), indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scrub_jsonl(text: str) -> str:
    """Redact parsed events so JSON escapes and credential keys survive."""
    lines = []
    for line in text.splitlines():
        try:
            lines.append(json.dumps(scrub_value(json.loads(line))))
        except ValueError:
            lines.append(scrub_text(line))
    return "\n".join(lines) + ("\n" if lines else "")


def retain_text(source: Path, destination: Path) -> None:
    text = source.read_text(encoding="utf-8", errors="replace")
    destination.write_text(scrub_jsonl(text) if source.suffix == ".jsonl" else scrub_text(text), encoding="utf-8")


def snapshot(ledger: Path, workdir: Path) -> dict[str, Any]:
    with closing(Ledger(ledger, read_only=True)) as store:
        # The proxy's lock serializes CLI writers; ledger read methods own transactions.
        tasks = [store.show(task["id"]) for task in store.list_tasks()]
        events = sorted((event for task in tasks for event in store.events(task["id"])), key=lambda e: e["seq"])
        findings = store.findings()
    return {"tasks": tasks, "events": events, "findings": findings,
            "files": {str(p.relative_to(workdir)): p.read_text(encoding="utf-8")
                      for p in sorted((workdir / "src").rglob("*.py")) if not p.is_symlink()}}


def ledger_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in ("tasks", "events", "findings")}


def hipson_proxy(workdir: str, expected_ledger: str) -> int:
    """Transparent CLI recorder. Both drivers and agent-run use this PATH entry."""
    root, db = Path(workdir), Path(expected_ledger)
    with (root / ".harness" / "cli.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = snapshot(db, root)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", "from hipson.cli import main; raise SystemExit(main())",
                               *sys.argv[1:]], capture_output=True, text=True, check=False)
        entry = {"args": sys.argv[1:], "exit_code": proc.returncode, "output": proc.stdout + proc.stderr,
                 "duration_seconds": time.monotonic() - started,
                 "ledger": os.environ.get("HIPSON_LEDGER"), "before": before, "after": snapshot(db, root)}
        with (root / ".harness" / "cli.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(scrub_value(entry)) + "\n")
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def runner_proxy(workdir: str) -> int:
    """Record the stock launcher's exit even when the lead's shell handles it."""
    root = Path(workdir)
    proc = subprocess.run(["bash", str(root / ".harness" / "agent-run.sh"), *sys.argv[1:]],
                          capture_output=True, text=True, check=False)
    entry = {"command": shlex.join(["examples/agent-run.sh", *sys.argv[1:]]),
             "exit_code": proc.returncode, "output": proc.stdout + proc.stderr}
    with (root / ".harness" / "runner.jsonl").open("a", encoding="utf-8") as log:
        fcntl.flock(log, fcntl.LOCK_EX)
        log.write(json.dumps(scrub_value(entry)) + "\n")
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


@dataclass
class Transcript:
    commands: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def parse_transcript(text: str) -> Transcript:
    """Codex exec --json events; item IDs identify updates, not extra commands."""
    result = Transcript()
    commands: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = scrub_value(json.loads(line))
            if not isinstance(event, dict):
                raise ValueError("event is not an object")
            kind = event.get("type")
            if kind == "turn.started":
                result.turns += 1
            if kind == "turn.completed":
                for key, value in event.get("usage", {}).items():
                    if type(value) is int:
                        result.usage[key] = result.usage.get(key, 0) + value
            if kind in ("error", "turn.failed"):
                result.errors.append(str(event.get("message", event.get("error", kind))))
            item = event.get("item", {})
            if kind in ("item.started", "item.updated", "item.completed") and item.get("type") == "command_execution":
                key = item.get("id", f"line-{number}")
                if not isinstance(key, str) or any(k in item and not isinstance(item[k], str)
                                                   for k in ("command", "aggregated_output")):
                    raise ValueError("invalid command item fields")
                if item.get("exit_code") is not None and type(item["exit_code"]) is not int:
                    raise ValueError("invalid command exit code")
                commands[key] = {**commands.get(key, {}), **item}
        except (ValueError, TypeError, AttributeError) as exc:
            result.errors.append(scrub_text(f"Invalid transcript line {number}: {exc}"))
    result.commands = list(commands.values())
    return result


def first_error(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return next((line for line in lines if re.search(r"error|refused|conflict|failed", line, re.I)),
                lines[0] if lines else "(no output)")


def metrics(transcript: Transcript, duration: float, audit: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # Audited CLI exits expose failures even inside a shell command that later exits zero.
    invocations = ([{"command": shlex.join(["hipson", *entry["args"]]),
                     "exit_code": entry["exit_code"], "aggregated_output": entry["output"]} for entry in audit]
                   if audit is not None else
                   [c for c in transcript.commands if re.search(r"\bhipson\b", c.get("command", ""))])
    failures = [{"command": c["command"], "exit_code": c["exit_code"],
                 "first_error_line": first_error(c.get("aggregated_output", ""))}
                for c in invocations if c.get("exit_code") not in (0, None)]
    return {"total_commands": len(transcript.commands), "hipson_invocations": len(invocations),
            "failing_invocations": failures,
            "help_lookups": sum(bool(re.search(r"(?:--help|\s-h)(?![\w-])", c.get("command", "")))
                                for c in transcript.commands),
            "docs_lookups": sum(bool(re.search(r"\b(?:cat|sed|head|less|rg)\b.*(?:docs/|\.md\b)",
                                              c.get("command", ""))) for c in transcript.commands),
            "turns": transcript.turns, "duration_seconds": round(duration, 3),
            "token_usage": transcript.usage or None}


class LedgerWatch:
    """Linux inotify observes even a delete/restore between CLI snapshots."""

    def __init__(self, path: Path):
        self.path = path
        self.identity = (path.stat().st_dev, path.stat().st_ino)
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        # CREATE, DELETE, MOVED_FROM/TO, DELETE_SELF, MOVE_SELF.
        if libc.inotify_add_watch(self.fd, os.fsencode(path.parent), 0xFC0) < 0:
            os.close(self.fd)
            raise OSError(ctypes.get_errno(), "inotify_add_watch")

    def failures(self) -> list[str]:
        violations = []
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(data):
                _, mask, _, length = struct.unpack_from("iIII", data, offset)
                name = data[offset + 16:offset + 16 + length].split(b"\0", 1)[0]
                if name == os.fsencode(self.path.name) or mask & (0x2C00 | 0x4000 | 0x8000):
                    violations.append("Ledger file was replaced or deleted (or its watch was lost)")
                offset += 16 + length
        if not self.path.exists() or (self.path.stat().st_dev, self.path.stat().st_ino) != self.identity:
            violations.append("Ledger file was replaced or deleted")
        return list(dict.fromkeys(violations))

    def close(self) -> None:
        os.close(self.fd)


@dataclass
class Context:
    workdir: Path
    ledger: Path
    transcript: Transcript
    state: dict[str, Any]
    initial: dict[str, Any]
    audit: list[dict[str, Any]]
    seed: dict[str, Any]
    runners: list[dict[str, Any]] = field(default_factory=list)

    def task(self) -> dict[str, Any] | None:
        return next((t for t in self.state["tasks"] if t["id"] == self.seed.get("task", "T-0001")), None)

    def events(self, kind: str, ref: str | None = None) -> list[dict[str, Any]]:
        return [e for e in self.state["events"] if e["type"] == kind and (ref is None or ref in e["refs"])]

    def transitions(self, status: str, task: str) -> list[dict[str, Any]]:
        found = []
        for entry in self.audit:
            previous = {t["id"]: t["status"] for t in entry["before"]["tasks"]}
            for current in entry["after"]["tasks"]:
                if current["id"] == task and current["status"] == status and previous.get(task) != status:
                    found.extend(e for e in entry["after"]["events"]
                                 if e["task"] == task and e["type"] == "status"
                                 and e["seq"] <= max((x["seq"] for x in self.state["events"]), default=0)
                                 and e["seq"] > max((x["seq"] for x in entry["before"]["events"]), default=0))
        return found


def overlaps(left: str, right: str) -> bool:
    return left == "." or right == "." or left == right or left.startswith(right + "/") or right.startswith(left + "/")


def visible_hipson_calls(command: str, workdir: Path | None = None, *, executed_only: bool = False) -> list[tuple[str, ...]]:
    """Recognize shell command positions, including Codex's bash -lc wrapper.

    This detects visible recorder bypasses, not concealed calls in arbitrary code.
    Only the CLI command/subcommand are compared; shell-expanded arguments vary.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        tokens = list(lexer)
    except ValueError:
        return []
    calls = []
    start = True
    for index, token in enumerate(tokens):
        if token and all(c in ";&|()\n" for c in token):
            if executed_only and (calls or not start or token.strip("\n") != ""):
                break
            start = True
            continue
        if not start:
            continue
        # Shell source is not an execution trace. Only the first unconditional
        # command has execution evidence; branches, loops and function bodies do
        # not. Do not attempt to evaluate arbitrary agent shell code here.
        if executed_only and token in ("if", "then", "else", "elif", "while", "until", "for", "case", "function", "do"):
            break
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", token) or token in ("env", "command", "exec", "then", "do", "if", "while", "until", "!"):
            continue
        start = False
        if Path(token).name in ("bash", "sh"):
            if index + 2 < len(tokens) and re.fullmatch(r"-[a-z]*c[a-z]*", tokens[index + 1]):
                calls.extend(visible_hipson_calls(tokens[index + 2], workdir, executed_only=executed_only))
        elif Path(token).name == "hipson":
            if workdir is not None and "/" in token and (workdir / token).resolve() != workdir / "bin/hipson":
                calls.append(("unrecorded executable", token))
                continue
            count = 2 if tokens[index + 1:index + 2] == ["task"] else 1
            args = tokens[index + 1:index + 1 + count]
            calls.append(tuple(arg for arg in args if not any(c in ";&|()\n" for c in arg)))
    return calls


def measurement_errors(ctx: Context) -> list[str]:
    errors = []
    expected = ledger_state(ctx.initial)
    for entry in ctx.audit:
        if ledger_state(entry["before"]) != expected:
            errors.append("Ledger changed outside recorded Hipson invocations")
        expected = ledger_state(entry["after"])
    if ledger_state(ctx.state) != expected:
        errors.append("Ledger changed outside recorded Hipson invocations")
    # Fake items after fake-0 are synthesized from the audit itself; count the script once.
    visible = Counter(call for item in ctx.transcript.commands
                      if not item.get("id", "").startswith("fake-") or item["id"] == "fake-0"
                      if type(item.get("exit_code")) is int
                      for call in visible_hipson_calls(item.get("command", ""), ctx.workdir, executed_only=True))
    recorded = Counter(tuple(entry["args"][:2 if entry["args"][:1] == ["task"] else 1]) for entry in ctx.audit)
    if visible - recorded:
        errors.append("Hipson call bypassed the recording proxy")
    return list(dict.fromkeys(errors))


def universal_checks(ctx: Context, oracle: Any) -> list[str]:
    failures = []
    commands = "\n".join(c.get("command", "") for c in ctx.transcript.commands)
    if re.search(r"\bsqlite3\b|\bpython\S*\b[^\n]*(?:HIPSON_LEDGER|ledger\.sqlite|\.connect\()", commands):
        failures.append("Direct ledger access/tampering is forbidden; use Hipson")
    for entry in ctx.audit:
        if entry["ledger"] != str(ctx.ledger):
            failures.append("HIPSON_LEDGER was overridden")
        before_status = {t["id"]: t["status"] for t in entry["before"]["tasks"]}
        for task in entry["after"]["tasks"]:
            if task["status"] == "done" and before_status.get(task["id"]) != "done":
                at_done = replace(ctx, state=entry["after"])
                if task["id"] != (ctx.task() or {}).get("id") or oracle.check(at_done):
                    failures.append(f"Task {task['id']} set to done before scenario acceptance was met")
    # Replay claim lifetimes across ALL tasks. A final empty claims table cannot hide an overlap.
    assignments = {a["id"]: (t["project"], a) for t in ctx.state["tasks"] for a in t["assignments"]}
    live: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in ctx.state["events"]:
        if event["type"] == "assigned" and event["refs"]:
            key = event["refs"][0]
            if key not in assignments:
                failures.append(f"Assignment {key} disappeared from the ledger")
                continue
            project, item = assignments[key]
            if item["mode"] == "rw":
                if any(project == other_project and any(overlaps(p, q) for p in item["scope"] for q in other["scope"])
                       for other_project, other in live.values()):
                    failures.append(f"Overlapping live rw claims at assignment {key}")
                live[key] = (project, item)
        elif event["type"] in ("finished", "reaped") and event["refs"]:
            live.pop(event["refs"][0], None)
    return list(dict.fromkeys(failures))


@dataclass
class Fixture:
    workdir: Path
    ledger: Path
    env: dict[str, str]
    helpers: list[subprocess.Popen[str]] = field(default_factory=list)
    worker_groups: set[int] = field(default_factory=set)

    def cli(self, *args: str) -> Any:
        proc = subprocess.run([str(self.workdir / "bin" / "hipson"), *args, "--json"], cwd=self.workdir,
                              env=self.env, capture_output=True, text=True, check=True, timeout=15)
        return json.loads(proc.stdout)

    def open_task(self, title: str = "Fix parser") -> str:
        return self.cli("task", "open", "--project", ".", "--title", title, "--objective", "Fix pair parsing",
                        "--acceptance", "Parser work is safely delegated", "--lead", "lead")["id"]

    def start_holder(self, task: str) -> str:
        log = (self.workdir / ".harness" / "holder.log").open("w", encoding="utf-8")
        try:
            proc = subprocess.Popen(["examples/agent-run.sh", "--task", task, "--actor", "holder",
                                     "--role", "builder", "--mode", "rw", "--scope", "src/parser",
                                     "--result-file", "holder.json", "--", "bin/fake-worker", "hold", "holder.json"],
                                    cwd=self.workdir, env=self.env, stdout=log, stderr=log, text=True,
                                    start_new_session=True)
        finally:
            log.close()
        self.helpers.append(proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = snapshot(self.ledger, self.workdir)
            items = [a for t in state["tasks"] for a in t["assignments"] if a["actor"] == "holder"]
            if items and (self.workdir / ".holder-ready").exists():
                self.worker_groups.add(items[0]["owner_pid"])
                return items[0]["id"]
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        raise RuntimeError("Conflict fixture writer failed to acquire its live claim")


def prepare_fixture(workdir: Path) -> Fixture:
    for directory in ("bin", "examples", "docs", "src/parser", ".harness", ".hipson", ".tmp"):
        (workdir / directory).mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(workdir)], check=True, capture_output=True, timeout=10)
    shutil.copy2(ROOT / "examples" / "agent-run.sh", workdir / ".harness" / "agent-run.sh")
    shutil.copy2(ROOT / "docs" / "TASK_LEDGER.md", workdir / "docs" / "TASK_LEDGER.md")
    shutil.copy2(ROOT / "e2e" / "agents" / "bin" / "fake-worker", workdir / "bin" / "fake-worker")
    (workdir / "src/parser/pairs.py").write_text('def parse_pair(text):\n    return tuple(text.split(","))\n')
    ledger = workdir / ".hipson" / "ledger.sqlite"
    with closing(Ledger(ledger)):
        pass
    proxy = workdir / "bin" / "hipson"
    proxy.write_text(f"#!{sys.executable}\nimport sys\nsys.path[:0] = {[str(ROOT), str(ROOT / 'src')]!r}\n"
                     f"from scripts.agent_e2e import hipson_proxy\nraise SystemExit(hipson_proxy({str(workdir)!r}, {str(ledger)!r}))\n")
    proxy.chmod(0o755)
    runner = workdir / "examples" / "agent-run.sh"
    runner_code = (f"import sys; sys.path[:0] = {[str(ROOT), str(ROOT / 'src')]!r}; "
                   f"from scripts.agent_e2e import runner_proxy; raise SystemExit(runner_proxy({str(workdir)!r}))")
    runner.write_text(f'#!/usr/bin/env bash\nexec {shlex.quote(sys.executable)} -c {shlex.quote(runner_code)} "$@"\n')
    runner.chmod(0o755)
    (workdir / ".harness" / "runner.jsonl").write_text("")
    # Fake shells and fixture workers have no reason to inherit caller credentials.
    # Codex receives only the authentication variables it needs, below.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "TERM", "TZ") or k.startswith("LC_")}
    # Sibling homes are removed with the temporary run and never retained by --keep.
    home = workdir.parent / "home"
    home.mkdir(mode=0o700)
    shell_env = home / ".bash_profile"
    shell_env.write_text(f"export PATH={shlex.quote(str(workdir / 'bin'))}:\"$PATH\"\n", encoding="utf-8")
    env.update(PATH=str(workdir / "bin") + os.pathsep + os.environ.get("PATH", os.defpath),
               HOME=str(home), BASH_ENV=str(shell_env), ENV=str(shell_env),
               SHLVL="1",  # Keep Bash's non-login startup path reading BASH_ENV.
               XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
               XDG_CACHE_HOME=str(home / ".cache"),
               PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT), HIPSON_LEDGER=str(ledger),
               HIPSON_HOME=str(workdir / ".hipson"), HIPSON_BIN=str(proxy),
               TMPDIR=str(workdir / ".tmp"), AGENT_RUN_GRACE_SECONDS="1")
    return Fixture(workdir, ledger, env)


def codex_command(executable: str, workdir: Path, model: str, effort: str, help_text: str) -> list[str]:
    missing = [flag for flag in ISOLATION_FLAGS if flag not in help_text]
    if missing:
        raise ValueError("Codex lacks required isolation flags: " + ", ".join(missing))
    if "astra" in model.lower():
        raise ValueError("Astra models are not allowed for this harness")
    return [executable, "exec", "--json", "-s", "workspace-write", "-C", str(workdir), "--model", model,
            *ISOLATION_FLAGS, "-c", f"model_reasoning_effort={json.dumps(effort)}",
            "-c", "approval_policy=\"never\"", "-c", "project_doc_max_bytes=0",
            "-c", 'shell_environment_policy.inherit="all"', "-"]


def execute(command: list[str], fixture: Fixture, stdin: str, timeout: float,
            stdout: Path, stderr: Path) -> tuple[int, bool]:
    # Unredacted driver output stays in anonymous temporary files, never in the
    # retained run directory (including on timeout or startup failures).
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as out, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as err:
        proc = subprocess.Popen(command, cwd=fixture.workdir, env=fixture.env, stdin=subprocess.PIPE,
                                stdout=out, stderr=err, text=True, start_new_session=True)
        timed_out = False
        try:
            proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2)
        finally:
            for source, destination in ((out, stdout), (err, stderr)):
                source.seek(0)
                text = source.read()
                destination.write_text(scrub_jsonl(text) if destination.suffix == ".jsonl" else scrub_text(text), encoding="utf-8")
        return proc.returncode, timed_out


def fake_transcript(script: str, code: int, output: str, audit: list[dict[str, Any]]) -> str:
    events: list[dict[str, Any]] = [{"type": "thread.started", "thread_id": "fake"}, {"type": "turn.started"}]
    for index, entry in enumerate([{"command": script, "exit_code": code, "output": output},
                                   *[{"command": shlex.join(["hipson", *e["args"]]), **e} for e in audit]]):
        events.append({"type": "item.completed", "item": {"id": f"fake-{index}", "type": "command_execution",
                       "command": entry["command"], "exit_code": entry["exit_code"],
                       "aggregated_output": entry["output"], "status": "completed"}})
    events.append({"type": "turn.completed"})
    return "".join(json.dumps(event) + "\n" for event in events)


def owned_worker_groups(workdir: Path, candidates: set[int]) -> set[int]:
    """Find surviving members even after their group leader has exited.

    Check every live member's cwd before signaling the group. A stale ledger PID
    alone never authorizes killing a process in another working directory.
    """
    owned, unsafe = set(), set()
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[-1].split()
            group = int(fields[2])
            if group not in candidates or fields[0] == "Z":
                continue
            try:
                if (process / "cwd").resolve(strict=True).is_relative_to(workdir):
                    owned.add(group)
                else:
                    unsafe.add(group)
            except FileNotFoundError:
                continue  # The member exited mid-scan; it must not veto cleanup of its group.
            except OSError:
                unsafe.add(group)
        except (OSError, ValueError, IndexError):
            continue  # A process can exit during discovery.
    return owned - unsafe - {os.getpgrp()}


def cleanup_workers(fixture: Fixture) -> None:
    try:
        state = snapshot(fixture.ledger, fixture.workdir)
        pids = [a["owner_pid"] for t in state["tasks"] for a in t["assignments"] if a["status"] == "active"]
    except (OSError, ValueError, sqlite3.Error):
        pids = []
    pids.extend(fixture.worker_groups)
    try:
        entries = read_jsonl(fixture.workdir / ".harness" / "cli.jsonl")
        pids.extend(a["owner_pid"] for entry in entries for t in entry["after"]["tasks"] for a in t["assignments"])
    except (OSError, ValueError):
        pass
    pids.extend(p.pid for p in fixture.helpers if p.poll() is None)
    candidates = {pid for pid in pids if type(pid) is int and pid > 0}
    groups = owned_worker_groups(fixture.workdir, candidates)
    for group in groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    for helper in fixture.helpers:
        try:
            helper.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            if helper.pid in owned_worker_groups(fixture.workdir, {helper.pid}):
                os.killpg(helper.pid, signal.SIGKILL)
            helper.wait(timeout=2)
    # A helper is often only the parent of the setsid'd runner group, so its exit proves nothing:
    # wait for the groups themselves (zombies count as gone) before and after escalating.
    if not wait_groups_gone(fixture.workdir, groups, deadline):
        for group in owned_worker_groups(fixture.workdir, groups):
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        wait_groups_gone(fixture.workdir, groups, time.monotonic() + 2)


def wait_groups_gone(workdir: Path, groups: set[int], deadline: float) -> bool:
    while owned_worker_groups(workdir, groups):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def run_scenario(name: str, output: Path, *, driver: str = "fake", solution: Path | None = None,
                 model: str = "gpt-6.1-sol", effort: str = "high", timeout: float = 300,
                 keep: bool = False) -> dict[str, Any]:
    scenario = SCENARIOS / name
    if name not in scenario_names():
        raise ValueError(f"Unknown scenario: {name}")
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"scenario": name, "driver": driver, "model": model if driver == "codex" else None,
                              "effort": effort if driver == "codex" else None, "failures": [],
                              "harness_errors": [], "isolation": []}
    started = time.monotonic()
    fixture = None
    watch = None
    audit: list[dict[str, Any]] = []
    transcript = Transcript()
    with tempfile.TemporaryDirectory(prefix="hipson-agent-e2e-") as temporary:
        workdir = Path(temporary) / "workspace"
        try:
            fixture = prepare_fixture(workdir)
            # Exercise the exact login-shell form used by sandbox command execution.
            probe = subprocess.run(["bash", "-lc", "command -v hipson"], cwd=workdir, env=fixture.env,
                                   capture_output=True, text=True, check=True, timeout=15)
            if probe.stdout.strip() != str(workdir / "bin" / "hipson"):
                raise ValueError("Login shell bypasses the recording hipson proxy")
            seed = load_module(scenario / "setup.py").setup(fixture)
            write_json(output / "seed.json", seed)
            initial = snapshot(fixture.ledger, workdir)
            write_json(output / "initial.json", initial)
            # Setup invocations are preconditions, not agent friction.
            (workdir / ".harness" / "cli.jsonl").write_text("")
            watch = LedgerWatch(fixture.ledger)
            mission = (scenario / "mission.md").read_text(encoding="utf-8")
            (output / "mission.md").write_text(mission, encoding="utf-8")
            if driver == "codex":
                executable = shutil.which("codex")
                if not executable:
                    raise ValueError("codex executable is not on PATH")
                help_result = subprocess.run([executable, "exec", "--help"], capture_output=True, text=True,
                                             check=True, timeout=15)
                command = codex_command(executable, workdir, model, effort, help_result.stdout)
                # A fresh home contains auth only: no owner AGENTS.md, skills, hooks, or config.
                home = Path(temporary) / "codex-home"
                home.mkdir(mode=0o700)
                auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
                if auth.is_file():
                    (home / "auth.json").symlink_to(auth.resolve())
                fixture.env["CODEX_HOME"] = str(home)
                fixture.env.update({key: os.environ[key] for key in AUTH_ENV if key in os.environ})
                result["isolation"] = [*ISOLATION_FLAGS, "project_doc_max_bytes=0", "fresh HOME",
                                       "login-shell PATH proxy", "fresh CODEX_HOME (auth link only)"]
                result["command"] = command
                code, timed_out = execute(command, fixture, mission, timeout, output / "transcript.jsonl", output / "stderr.txt")
            elif driver == "fake":
                script = (solution or scenario / "solution.sh").resolve()
                code, timed_out = execute(["bash", "-e", "-o", "pipefail", str(script)], fixture, "", timeout,
                                          output / "stdout.txt", output / "stderr.txt")
            else:
                raise ValueError(f"Unknown driver: {driver}")
            result.update(exit_code=code, timed_out=timed_out)
            retain_text(workdir / ".harness" / "cli.jsonl", output / "cli.jsonl")
            audit = read_jsonl(output / "cli.jsonl")
            retain_text(workdir / ".harness" / "runner.jsonl", output / "runner.jsonl")
            runners = read_jsonl(output / "runner.jsonl")
            result["worker_runs"] = runners
            if driver == "fake":
                (output / "transcript.jsonl").write_text(scrub_jsonl(fake_transcript(script.read_text(), code,
                    (output / "stdout.txt").read_text() + (output / "stderr.txt").read_text(), audit)), encoding="utf-8")
            transcript = parse_transcript((output / "transcript.jsonl").read_text(encoding="utf-8"))
            result["failures"].extend(transcript.errors)
            result["failures"].extend(watch.failures())
            final = snapshot(fixture.ledger, workdir)
            write_json(output / "final.json", final)
            ctx = Context(workdir, fixture.ledger, transcript, final, initial, audit, seed, runners)
            oracle = load_module(scenario / "oracle.py")
            result["harness_errors"].extend(measurement_errors(ctx))
            if not result["harness_errors"]:
                result["failures"].extend(universal_checks(ctx, oracle))
                result["failures"].extend(oracle.check(ctx))
            if code != 0:
                result["failures"].append(f"Driver exited {code}")
            if timed_out:
                result["failures"].append(f"Driver timed out after {timeout:g}s")
        except Exception as exc:
            result["harness_errors"].append(f"Harness error: {type(exc).__name__}: {exc}")
        finally:
            if watch:
                watch.close()
            if fixture:
                try:
                    cleanup_workers(fixture)
                except Exception as exc:
                    result["harness_errors"].append(f"Worker cleanup failed: {exc}")
                if keep:
                    # The auth home is a sibling, never copied into retained artifacts.
                    for log in (workdir / ".harness").iterdir():
                        if log.suffix in (".log", ".jsonl"):
                            retain_text(log, log)
                    shutil.copytree(workdir, output / "workspace", symlinks=True)
                    result["workspace"] = str(output / "workspace")
    result = scrub_value(result)
    result["metrics"] = scrub_value(metrics(transcript, time.monotonic() - started, audit))
    result["failures"] = list(dict.fromkeys(result["failures"]))
    result["harness_errors"] = list(dict.fromkeys(result["harness_errors"]))
    result["measurement_valid"] = not result["harness_errors"]
    result["passed"] = result["measurement_valid"] and not result["failures"]
    write_json(output / "result.json", result)
    return result


def scenario_names() -> list[str]:
    return sorted(path.name for path in SCENARIOS.iterdir() if (path / "mission.md").is_file())


def replay_scenario(saved: Path) -> dict[str, Any]:
    """Re-evaluate stored observations offline; never start drivers or cleanup."""
    previous = json.loads((saved / "result.json").read_text())
    name = previous["scenario"]
    if name not in scenario_names():
        raise ValueError(f"Unknown saved scenario: {name}")
    initial = json.loads((saved / "initial.json").read_text())
    final = json.loads((saved / "final.json").read_text())
    audit = read_jsonl(saved / "cli.jsonl")
    runners = read_jsonl(saved / "runner.jsonl")
    transcript = parse_transcript((saved / "transcript.jsonl").read_text())
    if (saved / "seed.json").exists():
        seed = json.loads((saved / "seed.json").read_text())
    elif name == "lead-flow":
        seed = {}
    else:
        task = initial["tasks"][0]
        seed = {"task": task["id"], "holder" if name == "conflict" else "stale": task["assignments"][0]["id"]}
    # Measurement checks use original paths from the snapshots; parser checks
    # only need a real cwd, since they execute the captured source from stdin.
    workdir = Path(final["tasks"][0]["project"])
    ledger = Path(audit[0]["ledger"]) if audit else workdir / ".hipson/ledger.sqlite"
    ctx = Context(workdir, ledger, transcript, final, initial, audit, seed, runners)
    errors = measurement_errors(ctx)
    failures = list(transcript.errors)
    oracle = load_module(SCENARIOS / name / "oracle.py")
    with tempfile.TemporaryDirectory(prefix="hipson-e2e-replay-") as temporary:
        if not errors:
            ctx = replace(ctx, workdir=Path(temporary))
            failures.extend(universal_checks(ctx, oracle))
            failures.extend(oracle.check(ctx))
    if previous.get("exit_code") != 0:
        failures.append(f"Driver exited {previous.get('exit_code')}")
    if previous.get("timed_out"):
        failures.append("Driver timed out")
    return scrub_value({**previous, "replay_of": str(saved), "harness_errors": errors,
                        "failures": list(dict.fromkeys(failures)), "measurement_valid": not errors,
                        "passed": not errors and not failures,
                        "metrics": metrics(transcript, previous["metrics"]["duration_seconds"], audit)})


def write_report(output: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    runs = scrub_value(runs)
    aggregates = {}
    for name in sorted({run["scenario"] for run in runs}):
        selected = [r for r in runs if r["scenario"] == name]
        valid = [r for r in selected if r.get("measurement_valid", True)]
        passed = sum(r["passed"] for r in valid)
        aggregates[name] = {"runs": len(selected), "valid_runs": len(valid), "invalid_runs": len(selected) - len(valid),
                            "passed": passed, "pass_rate": passed / len(valid) if valid else None}
    report = {"passed": all(run["passed"] for run in runs), "scenarios": aggregates, "runs": runs}
    write_json(output / "report.json", report)
    lines = ["# Agent e2e report", "", "| Scenario | Passed | Valid runs | Invalid runs | Pass rate |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for name, item in aggregates.items():
        rate = f"{item['pass_rate']:.0%}" if item["pass_rate"] is not None else "n/a"
        lines.append(f"| {name} | {item['passed']} | {item['valid_runs']} | {item['invalid_runs']} | {rate} |")
    for index, run in enumerate(runs, 1):
        m = run["metrics"]
        outcome = "INVALID" if not run.get("measurement_valid", True) else "PASS" if run["passed"] else "FAIL"
        lines.extend(["", f"## {index}. {run['scenario']}: {outcome}", "",
                      f"Commands: {m['total_commands']}; Hipson calls: {m['hipson_invocations']}; "
                      f"help/docs: {m['help_lookups']}/{m['docs_lookups']}; turns: {m['turns']}; "
                      f"duration: {m['duration_seconds']}s; tokens: {m['token_usage']}", ""])
        lines.extend(f"- {failure}" for failure in run["failures"])
        lines.extend(f"- Harness error: {error}" for error in run.get("harness_errors", []))
        lines.extend(f"- Exit {f['exit_code']}: {f['first_error_line']} (`{f['command']}`)"
                     for f in m["failing_invocations"])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *scenario_names()], default="all")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--driver", choices=["codex", "fake"], default="fake")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh"], default="high")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--solution", type=Path, help="Fake driver only: run this script instead of the reference")
    parser.add_argument("--replay", type=Path, help="Offline: recheck a saved run or report directory without launching a driver")
    args = parser.parse_args(argv)
    if args.runs < 1 or not 0 < args.timeout < float("inf"):
        parser.error("--runs and --timeout must be positive and finite")
    if "astra" in args.model.lower():
        parser.error("Astra models are not allowed")
    if args.solution and args.driver != "fake":
        parser.error("--solution requires --driver fake")
    output = ROOT / ".agent-e2e" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output.mkdir(parents=True)
    if args.replay:
        saved_runs = sorted(args.replay.glob("*/result.json")) if (args.replay / "report.json").is_file() else [args.replay / "result.json"]
        runs = [replay_scenario(path.parent) for path in saved_runs]
        if not runs:
            parser.error("--replay contains no saved runs")
        report = write_report(output, runs)
        print(f"Replay: {sum(run['passed'] for run in runs)}/{len(runs)} passed; report: {output / 'report.md'}")
        return 0 if report["passed"] else 1
    runs = []
    for name in scenario_names() if args.scenario == "all" else [args.scenario]:
        for number in range(1, args.runs + 1):
            run = run_scenario(name, output / f"{name}-{number}", driver=args.driver, solution=args.solution,
                               model=args.model, effort=args.effort, timeout=args.timeout, keep=args.keep)
            runs.append(run)
            outcome = "INVALID" if not run.get("measurement_valid", True) else "PASS" if run["passed"] else "FAIL"
            print(f"{name} #{number}: {outcome}", flush=True)
    report = write_report(output, runs)
    print(f"Report: {output / 'report.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
