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
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from hipson.ledger import Ledger  # noqa: E402
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


def _setup(workdir: Path) -> Any:
    fixture = prepare_fixture(workdir)
    (workdir / "tests").mkdir()
    (workdir / "tests" / "acceptance.py").write_text(ACCEPTANCE)
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
    result = str(lead.fixture.workdir / ".result.json")
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
            "done_without_review": sum(view["status"] == "done" and not any(
                item["role"] == "reviewer" and item["status"] == "passed" for item in view["assignments"])
                for view in views)}


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
    facts = ledger_facts(fixture) if strategy in ("B", "C") else {"done_without_review": None}
    workers = facts["workers"] if strategy in ("B", "C") else lead.calls.count("worker")
    checks = facts["checks_run"] if strategy in ("B", "C") else lead.calls.count("check")
    return {"case": name, "probe": probe, "strategy": strategy, "accepted": accepted, "ground_truth_ok": truth["ok"],
            "false_completion": accepted and not truth["ok"], "lost_good_work": not accepted and truth["ok"],
            "accepted_scope_violation": accepted and bool(truth["out_of_scope"]),
            "accepted_tampered_tests": accepted and not truth["tests_intact"],
            "accepted_without_independent_review": accepted and strategy != "C",
            "done_without_review_in_ledger": facts["done_without_review"],
            "lead_commands": len(lead.calls), "worker_runs": workers, "check_runs": checks,
            "duration_seconds": round(duration, 3), "tokens": None}


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
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="Write the JSON report here")
    parser.add_argument("--case", action="append", choices=[case[0] for case in CASES])
    parser.add_argument("--strategy", action="append", choices=STRATEGIES)
    args = parser.parse_args(argv)
    cases = [case for case in CASES if not args.case or case[0] in args.case]
    strategies = args.strategy or list(STRATEGIES)
    with tempfile.TemporaryDirectory(prefix="hipson-flow-compare-") as base:
        runs = [run_case(case, strategy, Path(base)) for case in cases for strategy in strategies]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout)
    report = {"artifact_kind": "hipson.flow_compare", "revision": revision, "dirty": dirty,
              "python": sys.version.split()[0], "model": None, "note": __doc__.split("\n\n")[2].strip(),
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
