#!/usr/bin/env python3
"""Offline, deterministic comparison of lead strategies over identical scripted worker behaviours.

Strategies (the lead's procedure; the workers are the same e2e/agents/bin/flow-worker profiles):
  A1 prompt/self-report  run the builder, accept its completion report (no Hipson)
  A2 prompt/self-verify  run the builder, run the visible check, accept when it passes (no Hipson)
  B  hipson task do      existing one-shot flow: open, run, declared check, close (with --observe)
  C  hipson task flow    build, verify, independent review, bounded repair, close (this release)

A hidden oracle judges the final repository: colon splitting and whitespace trimming both work, the
acceptance test is unchanged and nothing outside src/parser changed. This measures what each procedure
enforces against fixed behaviours. It does not measure how a live model follows a prompt, uses no model and
reports no tokens. Fixtures come from scripts/agent_e2e.prepare_fixture (isolated HOME, ledger and PATH).

  .venv/bin/python scripts/flow_compare.py --output .flow-compare/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import closing, suppress
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hipson.agent_runtimes import RUNTIMES, _result_payload, stdout_usage  # noqa: E402
from hipson.ledger import Ledger  # noqa: E402
from hipson.task_activity import safe_text  # noqa: E402
from scripts.agent_e2e import prepare_fixture  # noqa: E402

PY = sys.executable
WORKER = str(ROOT / "e2e" / "agents" / "bin" / "flow-worker")
TRIM = [PY, "tests/acceptance.py"]
SPLIT = [PY, "-c", "import sys; sys.path.insert(0, '.'); from src.parser.pairs import parse_pair; "
                   "assert parse_pair('a:b') == ('a', 'b')"]
ACCEPTANCE = ("import sys\nsys.path.insert(0, '.')\nfrom src.parser.pairs import parse_pair\n"
              "assert parse_pair('a:b') == ('a', 'b')\nassert parse_pair(' a : b ') == ('a', 'b')\n")
# (case, builder profile, visible check, what the case probes)
CASES = [
    ("correct", "correct", TRIM, "good work is accepted"),
    ("false-green", "false-green", TRIM, "a completion claim with no change"),
    ("check-repair", "needs-repair", TRIM, "a failing check the worker can fix with its output"),
    ("review-catch", "needs-repair", SPLIT, "a defect the visible check misses"),
    ("review-unfixed", "never-fixes", SPLIT, "a defect the worker never fixes"),
    ("tamper-tests", "tamper", TRIM, "the worker weakens the acceptance test"),
    ("crash", "crash", TRIM, "the worker exits non-zero"),
    ("malformed", "malformed", TRIM, "the worker writes a malformed result"),
    ("interrupted", "interrupt-once", TRIM, "the lead is interrupted mid-build and recovers"),
]
STRATEGIES = ("A1", "A2", "B", "C")


def _git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def _setup(workdir: Path, *, live: bool = False) -> Any:
    fixture = prepare_fixture(workdir)
    (workdir / "tests").mkdir()
    (workdir / "tests" / "acceptance.py").write_text(ACCEPTANCE)
    if live:
        (workdir / "tests/split.py").write_text(SPLIT[-1] + "\n", encoding="utf-8")
    with (workdir / ".gitignore").open("a") as handle:
        handle.write("src/parser/.interrupted-once\nsrc/parser/.worker-ready\n.result.json\n")
    _git(workdir, "add", ".", env=fixture.env)
    _git(workdir, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", "Acceptance",
         env=fixture.env)
    return fixture


def oracle(workdir: Path, env: dict[str, str]) -> dict[str, Any]:
    source = (workdir / "src/parser/pairs.py").read_text()
    probe = ("import json,sys; ns={}; exec(sys.stdin.read(), ns); "
             "print(json.dumps([ns['parse_pair']('a:b'), ns['parse_pair'](' a : b ')]))")
    try:
        values = json.loads(subprocess.run([PY, "-I", "-c", probe], input=source, capture_output=True, text=True,
                                           timeout=5, check=True, env={}).stdout)
        works = values == [["a", "b"], ["a", "b"]]
    except (ValueError, subprocess.SubprocessError):
        works = False
    tests_intact = (workdir / "tests/acceptance.py").read_text() == ACCEPTANCE
    changed = [line[3:] for line in _git(workdir, "status", "--porcelain", env=env).splitlines()]
    outside = [path for path in changed if not path.startswith("src/parser/")]
    return {"works": works, "tests_intact": tests_intact, "out_of_scope": outside,
            "ok": works and tests_intact and not outside}


class Lead:
    """Counts the commands a lead issues: each one is a tool call for a model-driven lead."""

    def __init__(self, fixture: Any) -> None:
        self.fixture, self.calls, self.env = fixture, [], {**fixture.env, "AGENT_RUN_GRACE_SECONDS": "1"}

    def run(self, argv: list[str], *, env: dict[str, str] | None = None, kind: str = "hipson") -> subprocess.CompletedProcess:
        self.calls.append(kind)
        return subprocess.run(argv, cwd=self.fixture.workdir, env={**self.env, **(env or {})}, capture_output=True,
                              text=True, timeout=300, check=False)

    def hipson(self, *args: str) -> subprocess.CompletedProcess:
        return self.run([PY, "-m", "hipson.cli", *args])

    def interrupt(self, argv: list[str], *, env: dict[str, str] | None = None, kind: str = "hipson") -> None:
        self.calls.append(kind)
        proc = subprocess.Popen(argv, cwd=self.fixture.workdir, env={**self.env, **(env or {})},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        ready = self.fixture.workdir / "src/parser/.worker-ready"
        deadline = time.monotonic() + 60
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        os.killpg(proc.pid, signal.SIGTERM) if kind == "worker" else proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=60)


def _worker_env(result: Path, stage: str = "build") -> dict[str, str]:
    return {"HIPSON_ROLE": "worker", "HIPSON_FLOW_RESULT": str(result), "HIPSON_FLOW_STAGE": stage}


def strategy_a(lead: Lead, profile: str, check: list[str], *, verify: bool, interrupt: bool) -> bool:
    result = lead.fixture.workdir / ".result.json"
    argv = [PY, WORKER, "builder", profile]
    if interrupt:
        lead.interrupt(argv, env=_worker_env(result), kind="worker")
    proc = lead.run(argv, env=_worker_env(result), kind="worker")
    try:
        report = json.loads(result.read_text())
    except (OSError, ValueError):
        return False
    accepted = proc.returncode == 0 and report.get("status") == "completed"
    if accepted and verify:
        accepted = lead.run(check, kind="check").returncode == 0
    return accepted


def strategy_b(lead: Lead, profile: str, check: list[str], *, interrupt: bool) -> bool:
    result = str(lead.fixture.workdir.parent / "do-result.json")  # Outside the observed project tree.
    do = [PY, "-m", "hipson.cli", "task", "do", "--title", "Fix pair parser", "--lead", "lead", "--check",
          shlex.join(check), "--actor", "builder", "--scope", "src/parser", "--observe", "--result-file", result,
          "--", PY, WORKER, "builder", profile]
    env = {"HIPSON_FLOW_RESULT": result}
    if not interrupt:
        return lead.run(do, env=env).returncode == 0
    lead.interrupt(do, env=env)
    # The documented manual recovery: reap, acknowledge, re-run the worker, close.
    lead.hipson("task", "reap", "--task", "T-0001")
    lead.hipson("task", "ack", "--assignment", "A-0001")
    Path(result).unlink(missing_ok=True)
    rerun = lead.run([PY, "-m", "hipson.cli", "task", "run", "--task", "T-0001", "--actor", "builder", "--role",
                      "builder", "--mode", "rw", "--scope", "src/parser", "--observe", "--result-file", result, "--",
                      PY, WORKER, "builder", profile], env=env)
    if rerun.returncode != 0:
        return False
    lead.hipson("task", "ack", "--assignment", "A-0002")
    return lead.hipson("task", "close", "--task", "T-0001").returncode == 0


def strategy_c(lead: Lead, profile: str, check: list[str], *, interrupt: bool) -> bool:
    spec = lead.fixture.workdir.parent / "spec.json"
    spec.write_text(json.dumps({
        "version": 1, "title": "Fix pair parser", "objective": "Split a:b and trim whitespace", "lead": "lead",
        "checks": [check], "close_to": "done", "check_timeout": 60,
        "builder": {"actor": "builder", "command": [PY, WORKER, "builder", profile], "scope": ["src/parser"]},
        "reviewer": {"actor": "reviewer", "command": [PY, WORKER, "reviewer", "strict"]}}))
    flow = [PY, "-m", "hipson.cli", "task", "flow", "--json", "--spec", str(spec)]
    if interrupt:
        lead.interrupt(flow)
        flow += ["--resume", "T-0001"]
    proc = lead.run(flow)
    try:
        return json.loads(proc.stdout)["state"] == "accepted"
    except (ValueError, KeyError):
        return False


def ledger_facts(fixture: Any) -> dict[str, Any]:
    if not fixture.ledger.exists():
        return {"workers": None, "checks_run": None, "done_without_review": None}
    with closing(Ledger(fixture.ledger, read_only=True)) as store:
        tasks = store.list_tasks()
        views = [store.show(task["id"]) for task in tasks]
        checks = sum(len(store.verifications(task["id"])) for task in tasks)
    return {"workers": sum(len(view["assignments"]) for view in views), "checks_run": checks,
            "done_without_review": sum(view["status"] == "done" and not view["review"]["accepted"] for view in views),
            # Measured from the ledger, not assumed from the strategy: an accepted independent review.
            "reviewed": any(view["review"]["accepted"] for view in views)}


def run_case(case: tuple[str, str, list[str], str], strategy: str, base: Path) -> dict[str, Any]:
    name, profile, check, probe = case
    workdir = base / f"{name}-{strategy}" / "repo"
    workdir.parent.mkdir(parents=True)
    fixture = _setup(workdir)
    lead = Lead(fixture)
    interrupt = name == "interrupted"
    started = time.monotonic()
    if strategy in ("A1", "A2"):
        accepted = strategy_a(lead, profile, check, verify=strategy == "A2", interrupt=interrupt)
    elif strategy == "B":
        accepted = strategy_b(lead, profile, check, interrupt=interrupt)
    else:
        accepted = strategy_c(lead, profile, check, interrupt=interrupt)
    duration = time.monotonic() - started
    truth = oracle(workdir, fixture.env)
    facts = ledger_facts(fixture) if strategy in ("B", "C") else {"done_without_review": None, "reviewed": False}
    workers = facts["workers"] if strategy in ("B", "C") else lead.calls.count("worker")
    checks = facts["checks_run"] if strategy in ("B", "C") else lead.calls.count("check")
    return {"case": name, "probe": probe, "strategy": strategy, "accepted": accepted, "ground_truth_ok": truth["ok"],
            "false_completion": accepted and not truth["ok"], "lost_good_work": not accepted and truth["ok"],
            "accepted_scope_violation": accepted and bool(truth["out_of_scope"]),
            "accepted_tampered_tests": accepted and not truth["tests_intact"],
            "accepted_without_independent_review": accepted and not facts["reviewed"],
            "done_without_review_in_ledger": facts["done_without_review"],
            "lead_commands": len(lead.calls), "worker_runs": workers, "check_runs": checks,
            "duration_seconds": round(duration, 3), "tokens": None, "usage_reason": "not_an_agent_runtime"}


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for strategy in STRATEGIES:
        mine = [run for run in runs if run["strategy"] == strategy]
        summary[strategy] = {
            "runs": len(mine),
            "correct_outcome": sum(run["accepted"] == run["ground_truth_ok"] for run in mine),
            "accepted": sum(run["accepted"] for run in mine),
            "false_completions": sum(run["false_completion"] for run in mine),
            "lost_good_work": sum(run["lost_good_work"] for run in mine),
            "accepted_scope_violations": sum(run["accepted_scope_violation"] for run in mine),
            "accepted_tampered_tests": sum(run["accepted_tampered_tests"] for run in mine),
            "accepted_without_independent_review": sum(run["accepted_without_independent_review"] for run in mine),
            "lead_commands": sum(run["lead_commands"] for run in mine),
            "worker_runs": sum(run["worker_runs"] for run in mine),
            "check_runs": sum(run["check_runs"] for run in mine),
            "duration_seconds": round(sum(run["duration_seconds"] for run in mine), 3),
            "tokens": None,
            "usage_reason": "not_an_agent_runtime",
        }
    return summary


LIVE_TITLE = "Fix pair parser"
LIVE_OBJECTIVE = "Make parse_pair split on ':' and trim whitespace around both parts."
LIVE_SCOPE = "src/parser"
LIVE_RULES = "Edit only src/parser. Leave the tests unchanged. Do not commit or push."
LIVE_STRATEGIES = ("A", "B", "C")
# The inline SPLIT probe cannot be expressed as Claude's exact Bash permission pattern.
LIVE_SPLIT = [PY, "tests/split.py"]
LIVE_CASES = {"fix": TRIM, "hidden-trim": LIVE_SPLIT}
MODEL_TIMEOUT = 180
CHECK_TIMEOUT = 60
LIVE_REPAIRS = 1
HIPSON = [PY, "-P", "-m", "hipson.cli"]
TOKEN_KEYS = ("input", "output", "cached_input", "cache_creation_input", "reasoning", "total")


def _live_text(value: Any, limit: int = 400) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    home = str(Path.home())
    if home != os.path.sep:
        value = re.sub(re.escape(home) + r"(?=/|[^\w./-]|$)", "~", value)
    value = re.sub(r"/(?:home|Users|mnt/[a-z]/Users)/[\w.-]+|/root(?=/|[^\w./-]|$)", "~", value)
    return safe_text(value.strip(), limit)


def _model_flags(options: argparse.Namespace, *, codex: bool = False) -> list[str]:
    flags = ["--model", options.model] if options.model else []
    if options.effort:
        flags += (["--config", f"model_reasoning_effort={json.dumps(options.effort)}"] if codex
                  else ["--effort", options.effort])
    return flags


def _live_command(strategy: str, workdir: Path, db: Path, check: list[str], options: argparse.Namespace,
                  *, dry_run: bool = False) -> tuple[list[str], str | None, dict[str, Any]]:
    builder = options.live
    check_text = shlex.join(check)
    brief = f"{LIVE_OBJECTIVE}\n{LIVE_RULES}\nVisible acceptance check: {check_text}\n"
    if strategy == "A":
        prompt = ("<prompt elided>" if dry_run else brief + 'Start your final answer with "Status: completed" if '
                  'the requested change is complete, or "Status: incomplete" otherwise. Then summarize your work.\n')
        flags = _model_flags(options, codex=builder == "codex")
        if builder == "claude":
            return [builder, "--print", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
                    "--allowedTools", f"Bash({check_text})", *flags], prompt, {}
        if builder == "codex":
            return [builder, "exec", "--json", "--sandbox", "workspace-write", "--cd", str(workdir), *flags, "-"], prompt, {}
        return [builder, "--print", prompt, "--output-format", "json", "--mode", "accept-edits", *flags], None, {}

    def worker(runtime: str, role: str) -> list[str]:
        return [*HIPSON, "agent", "exec", "--runtime", runtime, "--role", role, "--timeout", str(MODEL_TIMEOUT),
                *_model_flags(options)]

    task = [*HIPSON, "task", "--db", str(db)]
    if strategy == "B":
        brief_path, result = workdir.parent / "do-brief.md", workdir.parent / "do-result.json"
        if not dry_run:
            brief_path.write_text(brief, encoding="utf-8")
        return [*task, "do", "--json", "--title", LIVE_TITLE, "--objective", LIVE_OBJECTIVE, "--lead", "lead",
                "--check", check_text, "--timeout", str(CHECK_TIMEOUT), "--actor", "builder", "--scope", LIVE_SCOPE,
                "--observe", "--result-file", str(result), "--", *worker(builder, "builder"),
                "--brief", str(brief_path), "--result", str(result)], None, {}

    spec_path = workdir.parent / "spec.json"
    spec = {"version": 1, "title": LIVE_TITLE, "objective": LIVE_OBJECTIVE, "acceptance": [LIVE_RULES], "lead": "lead",
            "checks": [check], "check_timeout": CHECK_TIMEOUT, "max_repairs": LIVE_REPAIRS, "close_to": "done",
            "builder": {"actor": "builder", "command": worker(builder, "builder"), "scope": [LIVE_SCOPE]},
            "reviewer": {"actor": "reviewer", "command": worker(options.reviewer_runtime, "reviewer")}}
    if not dry_run:
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return [*task, "flow", "--json", "--spec", str(spec_path)], None, {
        "worker_argv": {role: spec[role]["command"] for role in ("builder", "reviewer")},
        "max_repairs": spec["max_repairs"], "close_to": spec["close_to"],
    }


def _json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_file(path: Path) -> dict[str, Any]:
    try:
        return _json_object(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}


def _count(*values: Any) -> int | None:
    return next((value for value in values if type(value) is int and value >= 0), None)


def _tokens(report: dict[str, Any]) -> dict[str, int | None] | None:
    usage = report.get("tokens")
    if usage is None:
        return None
    inp, cached = usage["input_tokens"], usage["cached_tokens"]
    if report.get("source") == "codex_rollout" and cached is not None:
        inp = inp - cached if inp is not None and inp >= cached else None
    return {
        "input": inp, "output": usage["output_tokens"], "cached_input": cached,
        "cache_creation_input": usage["cache_write_tokens"], "reasoning": usage["reasoning_tokens"],
        "total": usage["total_tokens"],
    }


def _known_total(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known and len(known) == len(values) else None


def _usage_evidence(report: dict[str, Any]) -> dict[str, Any]:
    return {"tokens": _tokens(report), "usage_reason": report.get("reason"), "usage_source": report.get("source")}


def _runtime_evidence(runtime: str, stdout: str, *, failed: bool = False) -> tuple[str | None, dict[str, Any]]:
    if runtime == "codex":
        answer = None
        turns = 0
        for line in stdout.splitlines():
            event = _json_object(line)
            if event.get("type") == "turn.completed":
                turns += 1
            if event.get("type") == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    answer = item.get("text")
    else:
        raw = _result_payload(runtime, stdout)
        payload = raw if isinstance(raw, dict) else {}
        if runtime == "claude":
            failed |= bool(payload.get("is_error") or payload.get("subtype", "success") != "success")
            answer = None if failed else payload.get("result")
        else:
            failed |= payload.get("status", "SUCCESS") != "SUCCESS"
            answer = None if failed else next((payload[key] for key in ("result", "response", "output", "text")
                                               if isinstance(payload.get(key), str)), None)
        turns = _count(payload.get("num_turns"))
    return answer if isinstance(answer, str) else None, {
        **_usage_evidence(stdout_usage(runtime, stdout, failed=failed)),
        "turns": (turns or None) if runtime == "codex" else turns,
    }


def _ledger_evidence(db: Path, strategy: str, workers: int | None) -> tuple[
        list[dict[str, Any]], int | None, str | None, list[dict[str, Any]]]:
    usage = {}
    if db.is_file():
        with closing(Ledger(db, read_only=True)) as store:
            usage = {row["id"]: json.loads(row["usage"]) if row["usage"] else {"reason": "runtime_error"}
                     for row in store.connection.execute("SELECT id, usage FROM assignments")}
    paths = ([db.parent / "do-result.json.meta.json"] if strategy == "B"
             else sorted((db.parent / f".{db.name}.flows").glob("*/*.result.json.meta.json")))
    records = [(path, meta) for path in paths if (meta := _json_file(path))]
    records.sort(key=lambda entry: str(entry[1].get("assignment", "")))
    sessions = []
    summary = None
    for path, meta in records:
        result = _json_file(path.with_suffix("").with_suffix(""))
        summary = meta.get("detail") or result.get("summary") or summary
        # The adapter can write preflight failures before it starts the model process.
        if meta.get("exit_code") is not None or meta.get("session"):
            runtime = meta.get("runtime")
            recorded = usage.get(meta.get("assignment"), {})
            sessions.append({"runtime": runtime if isinstance(runtime, str) and runtime in RUNTIMES else None,
                             "brief_bytes": _count(meta.get("brief_bytes")),
                             **_usage_evidence(recorded), "turns": _count(meta.get("num_turns"))})
    return sessions, len(sessions) if workers == len(records) else None, summary, [
        _usage_evidence(item) for item in usage.values()]


def _arm_usage(reports: list[dict[str, Any]]) -> dict[str, Any]:
    observed: dict[str, list[dict[str, int | None]]] = {}
    missing: dict[str, int] = {}
    for report in reports or [{"tokens": None, "usage_reason": "runtime_error"}]:
        if (counts := report["tokens"]) is not None:
            observed.setdefault(report["usage_source"], []).append(counts)
        else:
            reason = report["usage_reason"]
            missing[reason] = missing.get(reason, 0) + 1
    by_source = {
        source: {"sessions": len(items), **{key: _known_total([item[key] for item in items]) for key in TOKEN_KEYS}}
        for source, items in sorted(observed.items())
    }
    return {"tokens_by_source": by_source or None, "usage_missing_reasons": missing}


def _live_process(argv: list[str], workdir: Path, env: dict[str, str], stdin: str | None,
                  strategy: str) -> tuple[int, str, bool]:
    # Each native session has its own timeout. Leave time for Hipson's checks and cleanup between stages.
    stages = 2 * (1 + LIVE_REPAIRS) if strategy == "C" else 1
    timeout = MODEL_TIMEOUT if strategy == "A" else stages * (MODEL_TIMEOUT + CHECK_TIMEOUT + 60)
    windows = os.name == "nt"
    # A descendant that outlives its parent cannot keep a captured pipe open past the deadline.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace", dir=workdir.parent) as output:
        try:
            proc = subprocess.Popen(argv, cwd=workdir, env=env, text=True, encoding="utf-8",
                                    stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.DEVNULL, start_new_session=not windows)
        except OSError:
            return 127, "", False
        with proc:
            try:
                proc.communicate(stdin, timeout=timeout)
                code = proc.returncode
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                with suppress(ProcessLookupError):
                    if windows or strategy != "A":
                        proc.terminate()  # Hipson forwards the signal and cleans up its workers and ledger.
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        if windows:
                            proc.kill()
                        else:
                            os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                if isinstance(exc, KeyboardInterrupt):
                    raise
                code = 124
        output.seek(0)
        return code, output.read(), True


def _live_truth(workdir: Path, env: dict[str, str]) -> dict[str, Any]:
    try:
        truth = oracle(workdir, env)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        # Deleting the parser or the acceptance test is also a failed candidate.
        truth = {"ok": False, "tests_intact": True, "out_of_scope": []}
        try:
            status = _git(workdir, "status", "--porcelain", env=env)
            truth["out_of_scope"] = [line[3:] for line in status.splitlines() if not line[3:].startswith("src/parser/")]
        except (OSError, subprocess.SubprocessError):
            truth["out_of_scope"] = None
    try:
        truth["tests_intact"] = ((workdir / "tests/acceptance.py").read_bytes() == ACCEPTANCE.encode("utf-8")
                                 and (workdir / "tests/split.py").read_bytes() == (SPLIT[-1] + "\n").encode("utf-8"))
    except OSError:
        truth["tests_intact"] = False
    truth["ok"] = truth["ok"] and truth["tests_intact"]
    return truth


def _run_live(case: str, strategy: str, base: Path, options: argparse.Namespace) -> dict[str, Any]:
    workdir = base / f"{case}-{strategy}" / "repo"
    workdir.parent.mkdir(parents=True)
    fixture = _setup(workdir, live=True)
    fixture.ledger = workdir.parent / "ledger.sqlite"
    # Keep the native login/config environment and normal PATH, including HOME and CODEX_HOME. Hipson state
    # stays local, and caller task/git state must not redirect the commands away from this fixture.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HIPSON_", "GIT_"))}
    env.update(PYTHONPATH=fixture.env["PYTHONPATH"], HIPSON_HOME=str(workdir.parent / "hipson-home"),
               HIPSON_LEDGER=str(fixture.ledger), TMPDIR=str(workdir.parent), AGENT_RUN_GRACE_SECONDS="1")
    argv, stdin, _ = _live_command(strategy, workdir, fixture.ledger, LIVE_CASES[case], options)
    started = time.monotonic()
    code, stdout, launched = _live_process(argv, workdir, env, stdin, strategy)
    duration = round(time.monotonic() - started, 3)
    if strategy == "A":
        summary, evidence = _runtime_evidence(options.live, stdout, failed=code != 0)
        reports = [evidence] if launched else []
        claim = (summary or "").lstrip().split("\n", 1)[0].strip().casefold()
        accepted = code == 0 and claim == "status: completed"
        prompt = argv[2] if options.live == "agy" else stdin
        sessions = ([{"runtime": options.live, "brief_bytes": len((prompt or "").encode("utf-8")), **evidence}]
                    if launched else [])
        count, workers, checks = int(launched), int(launched), None
    else:
        result = _json_object(stdout)
        accepted = code == 0 and (result.get("closed") is True if strategy == "B" else result.get("state") == "accepted")
        facts = ledger_facts(fixture)
        workers, checks = facts["workers"], facts["checks_run"]
        sessions, count, summary, reports = _ledger_evidence(fixture.ledger, strategy, workers)
        reasons = result.get("reasons")
        if not accepted and isinstance(reasons, list) and reasons and isinstance(reasons[0], str):
            summary = reasons[0]
    truth = _live_truth(workdir, fixture.env)
    complete = count is not None
    scope_violation = None if truth["out_of_scope"] is None else bool(truth["out_of_scope"])
    return {"case": case, "strategy": strategy, "accepted": accepted, "ground_truth_ok": truth["ok"],
            "false_completion": accepted and not truth["ok"], "scope_violation": scope_violation,
            "tampered_tests": not truth["tests_intact"], "duration_seconds": duration, "exit_code": code,
            "model_sessions": count, "worker_runs": workers, "check_runs": checks,
            **_arm_usage(reports),
            "brief_bytes": _known_total([session["brief_bytes"] for session in sessions]) if complete else None,
            "turns": _known_total([session["turns"] for session in sessions]) if complete else None,
            "sessions": sessions, "summary": _live_text(summary)}


def _sample_stats(samples: dict[str, list[Any]]) -> dict[str, Any]:
    return {"reported": {key: len(values) for key, values in samples.items()},
            "sum": {key: round(sum(values), 3) if values else None for key, values in samples.items()},
            "median": {key: round(median(values), 3) if values else None for key, values in samples.items()}}


def _summarize_live(runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for strategy in LIVE_STRATEGIES:
        mine = [run for run in runs if run["strategy"] == strategy]
        metrics = ("accepted", "ground_truth_ok", "false_completion", "scope_violation", "tampered_tests",
                   "duration_seconds", "model_sessions", "worker_runs", "check_runs", "brief_bytes", "turns")
        samples = {key: [run[key] for run in mine if run[key] is not None] for key in metrics}
        token_samples: dict[str, dict[str, list[Any]]] = {}
        missing: dict[str, int] = {}
        for run in mine:
            for source, counts in (run["tokens_by_source"] or {}).items():
                counters = token_samples.setdefault(source, {key: [] for key in TOKEN_KEYS})
                for key in TOKEN_KEYS:
                    if counts[key] is not None:
                        counters[key].append(counts[key])
            for reason, count in run["usage_missing_reasons"].items():
                missing[reason] = missing.get(reason, 0) + count
        summary[strategy] = {"runs": len(mine), **_sample_stats(samples),
                             "tokens_by_source": {source: _sample_stats(counters)
                                                  for source, counters in sorted(token_samples.items())} or None,
                             "usage_missing_reasons": missing}
    return summary


def _runtime_version(runtime: str) -> str | None:
    try:
        proc = subprocess.run([runtime, "--version"], capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (proc.stdout.strip() or proc.stderr.strip()).splitlines()
    return _live_text(lines[0]) if proc.returncode == 0 and lines else None


def _report_context() -> dict[str, Any]:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout)
    return {"revision": revision, "dirty": dirty, "python": sys.version.split()[0]}


def _live_main(options: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    installed = {name for name in RUNTIMES if shutil.which(name)}
    options.reviewer_runtime = options.reviewer_runtime or next(
        (name for name in RUNTIMES if name != options.live and name in installed), options.live)
    config = {"artifact_kind": "hipson.flow_compare", "mode": "live", "runtime": options.live,
              "reviewer_runtime": options.reviewer_runtime, "model": _live_text(options.model),
              "effort": _live_text(options.effort)}
    cases = [case for case in LIVE_CASES if not options.live_case or case in options.live_case]
    if options.dry_run:
        plans = []
        for case in cases:
            for strategy in LIVE_STRATEGIES:
                workdir = Path("<temp>") / f"{case}-{strategy}" / "repo"
                argv, stdin, details = _live_command(strategy, workdir, workdir.parent / "ledger.sqlite",
                                                     LIVE_CASES[case], options, dry_run=True)
                if "worker_argv" in details:
                    details["worker_argv"] = {role: [_live_text(arg, 1600) for arg in worker]
                                              for role, worker in details["worker_argv"].items()}
                plans.append({"case": case, "strategy": strategy, "cwd": str(workdir),
                              "argv": [_live_text(arg, 1600) for arg in argv], "stdin": stdin,
                              "check_argv": [_live_text(arg, 1600) for arg in LIVE_CASES[case]], **details})
        print(json.dumps({**config, "dry_run": True, "plans": plans}, indent=2))
        return 0

    runtimes = list(dict.fromkeys((options.live, options.reviewer_runtime)))
    for runtime in runtimes:
        if runtime not in installed:
            parser.error(f"live runtime {runtime} is unavailable on PATH")
    versions = {runtime: _runtime_version(runtime) for runtime in runtimes}
    with tempfile.TemporaryDirectory(prefix="hipson-flow-live-") as base:
        runs = [_run_live(case, strategy, Path(base), options) for case in cases for strategy in LIVE_STRATEGIES]
    report = {**config, **_report_context(), "runtime_versions": versions,
              "note": "Input excludes reported cache reads. Total uses each runtime's own convention. "
                      "Counters aggregate the sessions reporting usage per runtime source. A counter missing from "
                      "any of those sessions stays null for that source. Strategy sums and medians use the "
                      "reported runs only, grouped by source for tokens.",
              "summary": _summarize_live(runs), "runs": runs}
    serialized = json.dumps(report, indent=2) + "\n"
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="Write the JSON report here")
    parser.add_argument("--case", action="append", choices=[case[0] for case in CASES])
    parser.add_argument("--strategy", action="append", choices=STRATEGIES)
    parser.add_argument("--live", choices=RUNTIMES, help="Compare bounded runs with a native agent runtime")
    parser.add_argument("--reviewer-runtime", choices=RUNTIMES)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--live-case", action="append", choices=LIVE_CASES)
    parser.add_argument("--dry-run", action="store_true", help="Print the live command plan without launching it")
    args = parser.parse_args(argv)
    if args.live:
        if args.case or args.strategy:
            parser.error("use --live-case with --live; all live strategies are compared")
        if args.model and "gpt-6-astra" in args.model.casefold():
            parser.error("gpt-6-astra is unavailable for this comparison")
        return _live_main(args, parser)
    if any((args.reviewer_runtime, args.model, args.effort, args.live_case, args.dry_run)):
        parser.error("live options require --live")
    cases = [case for case in CASES if not args.case or case[0] in args.case]
    strategies = args.strategy or list(STRATEGIES)
    with tempfile.TemporaryDirectory(prefix="hipson-flow-compare-") as base:
        runs = [run_case(case, strategy, Path(base)) for case in cases for strategy in strategies]
    report = {"artifact_kind": "hipson.flow_compare", **_report_context(),
              "model": None, "note": __doc__.split("\n\n")[2].strip(),
              "summary": summarize(runs), "runs": runs}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    width = max(len(case[0]) for case in cases)
    print(f"{'case':<{width}}  " + "  ".join(f"{s:<12}" for s in strategies))
    for case in cases:
        cells = []
        for strategy in strategies:
            run = next(item for item in runs if item["case"] == case[0] and item["strategy"] == strategy)
            mark = ("FALSE-DONE" if run["false_completion"] else "ok-done" if run["accepted"]
                    else "lost-work" if run["lost_good_work"] else "refused")
            cells.append(f"{mark:<12}")
        print(f"{case[0]:<{width}}  " + "  ".join(cells))
    print(json.dumps(report["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
