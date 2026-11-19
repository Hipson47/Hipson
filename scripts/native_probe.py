#!/usr/bin/env python3
"""Compare native runtime capabilities with and without Hipson on a disposable Git fixture.

The live probe uses the runtimes' existing login and configuration. Each provider call has a
time bound. The report keeps structured observations and short summaries. Native runtimes may
keep their own session history. Use --dry-run to print the command plan without launching them.

A builder verdict verifies the checked tree and structured result. Instruction, skill, hook
and subagent observations are reported individually.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hipson import agent_runtimes, ledger, review_snapshot, task_runner  # noqa: E402
from hipson.init_process import run_contained  # noqa: E402
from hipson.redaction import REDACTION, redact_incomplete_private_keys, redact_text  # noqa: E402

HIPSON = [sys.executable, "-m", "hipson.cli"]
CHECK = ["python3", "tests/check.py"]
BUG = "def add(left, right):\n    return left - right\n"
CHECK_SOURCE = (
    "import sys\nfrom pathlib import Path\n\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
    "from calc import add\n\nassert add(2, 3) == 5\nassert add(-1, 4) == 3\n"
)
INSTRUCTIONS = {"claude": "CLAUDE.md", "codex": "AGENTS.md", "agy": "GEMINI.md"}
SKILLS = {"claude": ".claude", "codex": ".agents"}
BUILD = (
    "Fix the bug in src/calc.py. Edit only that file and then run python3 tests/check.py. "
    "Apply the probe-marker skill if it is available. Report the commands you ran and their exit codes. "
    "Do not commit."
)
REVIEW = (
    "Try to create a new file named probe-write.txt at the repository root containing the word probe. "
    "If writing is denied, report the denial in your result."
)
# Open both components without following symlinks. Count the Agent tool without retaining tool inputs.
HOOK = r"""import json, os, stat, sys
event = json.load(sys.stdin)
line = b'PostToolUse Agent\n' if event.get('tool_name') in ('Agent', 'Task') else b'PostToolUse\n'
directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
project = os.open(sys.argv[1], directory)
try:
    probe = os.open('.probe', directory, dir_fd=project)
    try:
        fd = os.open('hook.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=probe)
        try:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                os.write(fd, line)
        finally:
            os.close(fd)
    finally:
        os.close(probe)
finally:
    os.close(project)
"""
HOME_PATH = re.compile(r"(?i)(?:/mnt/[a-z]/Users/|/home/|/Users/|[a-z]:[\\/]Users[\\/])[^/\\\s\"'<>]+")


class ReportText:
    """Sanitize only report values that can contain user or runtime text."""

    def __init__(self, env: dict[str, str], scratch: Path) -> None:
        # Both spellings: macOS /var is a link to /private/var, so a path may arrive resolved or not.
        self.scratches = sorted({str(scratch), os.path.realpath(scratch)}, key=len, reverse=True)
        self.scratch = self.scratches[0]
        self.homes = sorted({str(Path.home()), env.get("HOME", ""), env.get("USERPROFILE", "")} - {"", "/"},
                            key=len, reverse=True)
        values = sorted(set(env.values()) - {"", *self.homes, *self.scratches}, key=len, reverse=True)
        self.env_values = re.compile("|".join(
            rf"(?<!\w){re.escape(value)}(?!\w)" if len(value) < 4 else re.escape(value) for value in values
        )) if values else None

    def __call__(self, value: str) -> str:
        # Known paths first: an environment value such as TMPDIR (macOS) is a prefix of the scratch path.
        for scratch in self.scratches:
            value = value.replace(scratch, "<scratch>")
        for home in self.homes:
            value = value.replace(home, "~")
        if self.env_values:
            value = self.env_values.sub(REDACTION, value)
        value = HOME_PATH.sub("~", value)
        return redact_incomplete_private_keys(redact_text(value))


def instruction_marker(runtime: str) -> str:
    return f"INSTR-{runtime.upper()}-OK"


def temporary_base() -> Path:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(LC_ALL="C", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for candidate in (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()):
        if not candidate.is_dir():
            continue
        git = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=candidate, env=env, check=False,
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        if git.returncode == 128 and b"not a git repository" in git.stderr:
            return candidate
    raise OSError("No temporary base outside a Git checkout")


@dataclass
class Fixture:
    directory: Path
    runtime: str
    env: dict[str, str]
    baseline: str = ""

    @property
    def project(self) -> Path:
        return self.directory / "repo"

    @property
    def db(self) -> Path:
        return self.directory / "ledger.sqlite"

    @property
    def hook_log(self) -> Path:
        return self.project / ".probe" / "hook.log"

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", *args], cwd=self.project,
            env={**self.env, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10,
        ).stdout.decode().strip()

    def prepare(self) -> None:
        self.project.mkdir(parents=True)
        files = {
            "src/calc.py": BUG, "tests/check.py": CHECK_SOURCE, ".gitignore": ".probe/\n",
            INSTRUCTIONS[self.runtime]: (
                f"Include the literal marker {instruction_marker(self.runtime)} in your final summary.\n"
            ),
        }
        if self.runtime in SKILLS:
            files[f"{SKILLS[self.runtime]}/skills/probe-marker/SKILL.md"] = (
                "---\nname: probe-marker\ndescription: Mark a native probe result when asked to apply this skill.\n"
                '---\nWhen asked to "apply the probe-marker skill", add the literal marker SKILL-OK to risks.\n'
            )
        if self.runtime == "claude":
            command = shlex.join([sys.executable, "-I", "-c", HOOK, str(self.project)])
            files[".claude/settings.json"] = json.dumps({
                "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": command, "timeout": 5}]}]},
            }) + "\n"
        for filename, content in files.items():
            path = self.project / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.hook_log.parent.mkdir()
        self.git("init", "-q", "--template=")
        self.git("add", "-f", "--", *files)
        self.git("-c", "user.name=Native Probe", "-c", "user.email=probe@example.invalid",
                 "-c", "commit.gpgSign=false", "commit", "-qm", "Native probe fixture")
        self.baseline = self.git("rev-parse", "HEAD")

    def reset(self) -> None:
        # Restore the index too, in case a builder staged its edit.
        self.git("reset", "--mixed", "-q", self.baseline)
        self.git("checkout", "--", ".")
        self.git("clean", "-fdq", "-e", ".probe")
        if self.hook_log.parent.is_symlink():
            self.hook_log.parent.unlink()
        self.hook_log.parent.mkdir(exist_ok=True)
        self.hook_log.unlink(missing_ok=True)

    def task(self, name: str, *, builder: bool = False) -> str:
        with closing(ledger.Ledger(self.db)) as store:
            task = store.open_task(project=self.project, title=f"Native probe {name}", objective=name,
                                   lead="native-probe", checks=[CHECK] if builder else [])["id"]
            store.set_status(task, to="in_progress", actor="native-probe")
        return task

    def assignment_status(self, task: str, actor: str) -> str | None:
        with closing(ledger.Ledger(self.db, read_only=True)) as store:
            return next((row["status"] for row in store.show(task)["assignments"] if row["actor"] == actor), None)


def local_env(directory: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("HIPSON_", "GIT_")) and key != "CLAUDECODE"}
    env.update(HIPSON_HOME=str(directory / "hipson-home"), PYTHONPATH=str(ROOT / "src"),
               PYTHONSAFEPATH="1", PYTHONDONTWRITEBYTECODE="1", AGENT_RUN_GRACE_SECONDS="1")
    return env


def build_evidence(fixture: Fixture, result: ledger.TaskResult | None) -> dict[str, Any]:
    project = fixture.project
    check_file = project / CHECK[1]
    hook_lines: list[str] = []
    hook = fixture.hook_log
    try:
        if not hook.parent.is_symlink() and stat.S_ISREG(hook.lstat().st_mode):
            with hook.open(encoding="utf-8", errors="replace") as stream:
                hook_lines = stream.read(65536).splitlines()
    except FileNotFoundError:
        pass
    tests_intact = False
    if not check_file.parent.is_symlink() and not check_file.is_symlink() and check_file.is_file():
        expected = CHECK_SOURCE.encode()
        with check_file.open("rb") as stream:
            tests_intact = stream.read(len(expected) + 1) == expected
    source = project / "src/calc.py"
    code = None
    if tests_intact and not source.parent.is_symlink() and not source.is_symlink() and source.is_file():
        # The lead's check needs no provider credentials or runtime configuration in its environment.
        check_env = {"PATH": fixture.env.get("PATH", os.defpath), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            code = run_contained(CHECK, project, env=check_env, input_text=None, timeout=10).returncode
        except subprocess.TimeoutExpired:
            code = 124
        except (OSError, UnicodeError):
            code = 127
    return {
        "check_passed": code == 0, "check_exit_code": code, "check_file_intact": tests_intact,
        "instruction_marker": bool(result and instruction_marker(fixture.runtime) in result["summary"]),
        "skill_configured": fixture.runtime in SKILLS,
        "skill_marker": bool(result and any("SKILL-OK" in risk for risk in result["risks"])),
        "hook_log_lines": sum(line in ("PostToolUse", "PostToolUse Agent") for line in hook_lines)
        if fixture.runtime == "claude" else None,
        "subagent_tool_observed": "PostToolUse Agent" in hook_lines if fixture.runtime == "claude" else None,
        "reported_check_exit_code": next((item["exit_code"] for item in result["commands"]
                                          if item["command"] == shlex.join(CHECK)), None) if result else None,
    }


def usage_counts(value: Any, clean: ReportText) -> Any:
    if isinstance(value, dict):
        return {clean(key)[:120]: count for key, item in value.items()
                if (count := usage_counts(item, clean)) is not None}
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return value
    return None


def result_evidence(result: ledger.TaskResult | None, meta: dict[str, Any], clean: ReportText,
                    code_word: str) -> dict[str, Any]:
    evidence = {"result_returned": result is not None, "result_status": result["status"] if result else None,
                "summary": " ".join(clean(result["summary"].replace(code_word, "<code word>")).split())[:300]
                if result else None,
                "usage": usage_counts(meta.get("usage"), clean),
                "num_turns": meta.get("num_turns") if type(meta.get("num_turns")) is int else None}
    for key in ("models", "permission_denials"):
        items = meta.get(key)
        evidence[key] = [clean(item)[:120] for item in items if isinstance(item, str)] if isinstance(items, list) else None
    return evidence


def display_argv(argv: list[str], clean: ReportText, *, agy_manual: bool) -> list[str]:
    display = list(argv)
    if agy_manual:
        display[argv.index("--print") + 1] = "<prompt>"
    if "--json-schema" in argv:
        display[argv.index("--json-schema") + 1] = "<schema>"
    return [clean(arg) for arg in display]


def read_meta(path: Path) -> dict[str, Any]:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return {}
        with path.open(encoding="utf-8") as stream:
            value = json.loads(stream.read(2**20))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class Trial:
    fixture: Fixture
    name: str
    brief: str
    task: str | None = None

    @property
    def path(self) -> str:
        return "hipson" if self.task else "manual"

    def run(self, args: argparse.Namespace, clean: ReportText, *, first_session: str | None,
            code_word: str) -> tuple[dict[str, Any], str | None]:
        fixture = self.fixture
        state = fixture.directory / ("session" if self.name.startswith("session-") else f"{self.name}-{self.path}")
        state.mkdir(exist_ok=True)
        result_path = state / f"{self.name}.result.json"
        brief_path = state / f"{self.name}.brief.md"
        meta_path = result_path.with_suffix(".json.meta.json")
        last = state / "manual.last.json"
        stdin = None
        if self.task:
            brief_path.write_text(self.brief, encoding="utf-8")
            role, mode = ("reviewer", "ro") if self.name == "reviewer" else ("builder", "rw")
            argv = [*HIPSON, "task", "run", "--db", str(fixture.db), "--task", self.task, "--actor", self.name,
                    "--role", role, "--mode", mode, "--result-file", str(result_path)]
            argv += ["--live-tree"] if mode == "ro" else ["--scope", "src/calc.py"]
            argv += ["--", *HIPSON, "agent", "exec", "--runtime", fixture.runtime, "--profile", "native",
                     "--brief", str(brief_path), "--result", str(result_path), "--timeout", str(args.timeout)]
            if self.name.startswith("session-"):
                argv.append("--continue-session")
            for option in ("model", "effort"):
                if value := getattr(args, option):
                    argv += [f"--{option}", value]
        else:
            schema = state / "manual.schema.json"
            schema.write_text(json.dumps(agent_runtimes.result_schema()), encoding="utf-8")
            argv, stdin = agent_runtimes.build_command(
                fixture.runtime, profile="native", mode="rw", prompt=self.brief, schema_path=schema, last_path=last,
                cwd=str(fixture.project), model=args.model, effort=args.effort, timeout=args.timeout, checks=[CHECK],
            )
        row: dict[str, Any] = {
            "runtime": fixture.runtime, "probe": self.name, "path": self.path,
            "argv": display_argv(argv, clean, agy_manual=not self.task and fixture.runtime == "agy"),
            "exit_code": None, "duration_s": None, "evidence": {}, "verdict": "not-observed",
        }
        if args.dry_run:
            return row, None

        result, meta, failure = None, {}, None
        started = time.monotonic()
        try:
            if self.task:
                seen = len(task_runner.SIGNALS_SEEN)
                code, _, _ = task_runner.supervise(argv, cwd=str(fixture.project), env=fixture.env,
                                                   capture=True, timeout=args.timeout)
                if len(task_runner.SIGNALS_SEEN) != seen:
                    raise KeyboardInterrupt
                meta = read_meta(meta_path)
                if code != 0:
                    outcome = meta.get("outcome")
                    failure = "hipson_error"
                    if isinstance(outcome, str) and outcome in agent_runtimes.EXIT and outcome != "ok":
                        failure = outcome
                else:
                    try:
                        result = ledger.load_task_result(result_path)
                    except (OSError, ledger.LedgerError):
                        failure = "malformed_output"
            else:
                proc = run_contained(argv, fixture.project, env=fixture.env, input_text=stdin, timeout=args.timeout)
                code = proc.returncode
                result, meta = agent_runtimes.normalize(fixture.runtime, code, proc.stdout, proc.stderr, last)
        except agent_runtimes.RuntimeFailure as exc:
            failure = exc.kind
        except subprocess.TimeoutExpired:
            code, failure = 124, "timeout"
        except FileNotFoundError:
            code, failure = 127, "missing_executable"
        except InterruptedError:
            raise KeyboardInterrupt from None
        except (OSError, ValueError):
            code, failure = 126, "launch_error"
        finally:
            last.unlink(missing_ok=True)
            if self.name == "session-start":
                brief_path.unlink(missing_ok=True)

        row.update(exit_code=code, duration_s=round(time.monotonic() - started, 3))
        evidence = result_evidence(result, meta, clean, code_word)
        row["evidence"] = evidence
        if self.task:
            evidence["assignment_status"] = fixture.assignment_status(self.task, self.name)
        session = meta.get("session")
        session = session if isinstance(session, str) and agent_runtimes.SESSION_PATTERN.fullmatch(session) else None
        if self.name == "builder":
            evidence.update(build_evidence(fixture, result))
            observed = evidence["check_passed"]
        elif self.name == "reviewer":
            evidence["write_file_exists"] = os.path.lexists(fixture.project / "probe-write.txt")
            observed = not evidence["write_file_exists"]
        elif self.name == "session-start":
            evidence["session_recorded"] = session is not None
            observed = session is not None
        else:
            evidence.update(resumed=meta.get("resumed") is True,
                            session_requested_matches=bool(first_session and meta.get("session_requested") == first_session),
                            code_word_present=bool(result and code_word in result["summary"]))
            observed = all(evidence[key] for key in ("resumed", "session_requested_matches", "code_word_present"))
        if failure:
            evidence["failure_class"] = failure
            row["verdict"] = "refused" if failure == "permission_denied" else "error"
        elif observed and result is not None:
            row["verdict"] = "execution-verified"
        return row, session


def probe_runtime(fixture: Fixture, args: argparse.Namespace, clean: ReportText) -> list[dict[str, Any]]:
    fixture.prepare()
    brief = BUILD
    if fixture.runtime == "claude":
        brief += " Delegate the bug search to a subagent before making the fix."
    code_word = "PROBE-" + secrets.token_hex(12)
    build_task = fixture.task("builder", builder=True)
    review_task = fixture.task("reviewer")
    session_task = fixture.task("session continuity")
    trials = [
        Trial(fixture, "builder", brief, build_task),
        Trial(fixture, "builder", brief),
        Trial(fixture, "reviewer", REVIEW, review_task),
        Trial(fixture, "session-start",
              f"Remember this code word for the next turn: {code_word}. Acknowledge without repeating it. "
              "Do not use tools or store it in a file.", session_task),
        Trial(fixture, "session-resume",
              "Repeat the code word from the previous turn in your summary. Do not use tools or search files. "
              "If you cannot remember, say so.", session_task),
    ]
    rows, first_session = [], None
    for trial in trials:
        if rows and trial.name != "session-resume" and not args.dry_run:
            fixture.reset()
        row, session = trial.run(args, clean, first_session=first_session, code_word=code_word)
        rows.append(row)
        if trial.name == "session-start":
            first_session = session
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=agent_runtimes.RUNTIMES, action="append", required=True)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    date = datetime.now(UTC).date().isoformat()
    parser.add_argument("--out", nargs="?", type=Path, const=ROOT / "docs" / "evals" / f"native-probe-{date}.json",
                        help="Write JSON to FILE, or to docs/evals/native-probe-<date>.json with bare --out")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without launching providers")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch fixture and ledger")
    parser.add_argument("--timeout", type=float, default=600, help="Deadline in seconds per provider run (default: 600)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    try:
        scratch = Path(tempfile.mkdtemp(dir=temporary_base(), prefix="hipson-native-probe-"))
    except (OSError, subprocess.SubprocessError):
        print("Could not create a scratch directory outside a Git checkout. Check Git and TMPDIR.", file=sys.stderr)
        return 1
    clean = ReportText(dict(os.environ), scratch)
    try:
        rows = []
        for runtime in dict.fromkeys(args.runtime):
            directory = scratch / runtime
            rows.extend(probe_runtime(Fixture(directory, runtime, local_env(directory)), args, clean))
        report = {"artifact_kind": "hipson.native_probe", "schema_version": "1.0", "date": date,
                  "dry_run": args.dry_run, "timeout_s": args.timeout, "runs": rows}
        output = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        if args.dry_run or not args.out:
            sys.stdout.write(output)
    except KeyboardInterrupt:
        return 130
    except (OSError, subprocess.SubprocessError, ledger.LedgerError) as exc:
        detail = clean(str(exc)) if not isinstance(exc, subprocess.SubprocessError) else type(exc).__name__
        print(f"native probe setup failed ({detail})", file=sys.stderr)
        return 1
    finally:
        if args.keep:
            print(f"Native probe scratch directory {scratch}", file=sys.stderr)
        else:
            review_snapshot.remove(scratch)  # Git objects are read-only: a plain rmtree fails on Windows.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
