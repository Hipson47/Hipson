#!/usr/bin/env python3
"""Measure Hipson's CLI, MCP and compact projection reads offline."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import closing, redirect_stderr, redirect_stdout
from functools import partial
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from unittest.mock import patch

from hipson import __version__, cli, ledger, mcp_server, task_verification

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures.agent_ux_ledger import build  # noqa: E402

READS = ("task.board", "task.now", "lead.resume", "task.show")


def _cli(args: list[str]) -> str:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = cli.main(args)
        except SystemExit as exc:  # argparse's normal --help path
            code = exc.code
    if code:
        raise RuntimeError(f"CLI {' '.join(args)} failed ({code}): {stderr.getvalue().strip()}")
    return stdout.getvalue()


def _mcp(method: str, params: dict[str, Any]) -> dict[str, Any]:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    response = mcp_server._handle_request(request, project=ROOT)
    if response is None or "result" not in response:
        raise RuntimeError(f"MCP {method} failed: {response}")
    result = response["result"]
    if result.get("isError"):
        raise RuntimeError(f"MCP {params.get('name', method)} failed: {result['content']}")
    return result


def _tool_text(name: str, arguments: dict[str, str]) -> str:
    result = _mcp("tools/call", {"name": name, "arguments": arguments})
    return result["content"][0]["text"]


def _measure(read: Callable[[], str]) -> dict[str, Any]:
    with patch.object(task_verification, "project_state", wraps=task_verification.project_state) as fingerprint:
        start = perf_counter()
        output = read()
        elapsed = perf_counter() - start
    return {"bytes": len(output.encode("utf-8")), "project_state_calls": fingerprint.call_count,
            "wall_seconds": round(elapsed, 6)}


def _measure_read(name: str, task: str, *, mcp_detail: bool) -> dict[str, Any]:
    command = name.split(".")
    cli_detail = bool(re.search(r"(?<!\S)--detail(?:[\s={]|$)", _cli([*command, "--help"])))
    arguments = {"task": task} if name == "task.show" else {}
    if arguments:
        command.extend(["--task", task])
    result = {}
    for detail in ("full", "compact"):
        cli_available = detail == "full" or cli_detail
        mcp_available = detail == "full" or mcp_detail
        cli_args = [*command, "--detail", detail] if cli_detail else command
        mcp_args = {**arguments, "detail": detail} if mcp_detail else arguments
        result[detail] = {
            "available": cli_available or mcp_available,
            "cli_json": _measure(partial(_cli, [*cli_args, "--json"])) if cli_available else None,
            "mcp_json": _measure(partial(_tool_text, name, mcp_args)) if mcp_available else None,
            "cli_text": _measure(partial(_cli, cli_args)) if cli_available else None,
        }
    return result


def _select_task(db: Path) -> str:
    """Pick a live task with a large saved inventory without fingerprinting candidate projects."""
    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        tasks = store.list_tasks()
        live = [task for task in tasks if task["status"] not in ("done", "cancelled")]
        if not tasks:
            raise ValueError("The ledger has no task to measure with task show")

        sizes = {}
        if store.connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'verifications'").fetchone():
            sizes = dict(store.connection.execute(
                """SELECT task, CASE WHEN json_valid(evidence)
                         THEN COALESCE(json_array_length(evidence, '$.files'), 0) ELSE 0 END
                   FROM verifications WHERE seq IN (SELECT MAX(seq) FROM verifications GROUP BY task)"""
            ))
        return max(live or tasks, key=lambda task: sizes.get(task["id"], 0))["id"]


def _measure_projection(db: Path, task: str) -> dict[str, Any]:
    from hipson import task_projection

    board_tasks: Sequence[task_projection.TaskProjection | task_projection.TaskStub] = ()

    def render_board() -> str:
        nonlocal board_tasks
        board = task_projection.project_list(store, kind="task.board")
        board_tasks = board["tasks"]
        return task_projection.dumps(board)

    with closing(ledger.Ledger(db, create=False, read_only=True)) as store:
        metrics = {
            "task.board": _measure(render_board),
            "task.now": _measure(lambda: task_projection.dumps(task_projection.project_list(store, kind="task.now"))),
            "task.show": _measure(lambda: task_projection.dumps(task_projection.project_task(store, task))),
        }
    distribution: dict[str, dict[str, int]] = {}
    largest = 0
    for item in board_tasks:
        if "too_large" in item:
            continue
        largest = max(largest, len(task_projection.dumps(item).encode("utf-8")))
        for check in item["checks"]:
            counts = distribution.setdefault(check["recorded"], {})
            current = check["current"]
            counts[current] = counts.get(current, 0) + 1
    metrics["task.board"].update(check_states=distribution, largest_task_bytes=largest)
    return metrics


def measure(db: Path, *, task: str | None = None, ledger_source: str = "provided",
            projection: bool = False) -> dict[str, Any]:
    db = db.expanduser().resolve(strict=True)
    task = task or _select_task(db)
    revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "--no-optional-locks", "-C", str(ROOT), "-c", "core.fsmonitor=false", "status",
         "--porcelain", "--untracked-files=normal"],
        capture_output=True, check=True, timeout=10,
    ).stdout)
    report = {"schema_version": 1, "hipson_version": __version__, "git_revision": revision, "git_dirty": dirty,
              "ledger_source": ledger_source, "task_show_id": task}
    if projection:
        return {**report, "projection": _measure_projection(db, task)}
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        detail_tools = {tool["name"] for tool in _mcp("tools/list", {})["tools"]
                        if "detail" in tool["inputSchema"].get("properties", {})}
        reads = {name: _measure_read(name, task, mcp_detail=name in detail_tools) for name in READS}
    return {**report, "reads": reads}


def _resume_e2e(path: Path) -> dict[str, Any]:
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    resume = [run for run in runs if run["scenario"] == "resume" and run["driver"] == "fake"]
    if len(resume) != 1 or not resume[0].get("passed") or not resume[0].get("measurement_valid"):
        raise ValueError("Expected exactly one passing, valid resume run with the fake driver in the e2e report")
    run = resume[0]
    return {key: run[key] for key in ("scenario", "driver", "passed", "measurement_valid", "metrics")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Existing SQLite ledger (default: generate a temporary fixture)")
    parser.add_argument("--task", help="Task ID to use for task show (default: select a heavy task)")
    parser.add_argument("--projection", action="store_true", help="Measure the compact projection core directly")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path (default: stdout)")
    parser.add_argument("--resume-e2e-report", type=Path, help="Include metrics from agent_e2e.py's report.json")
    args = parser.parse_args(argv)
    try:
        resume = _resume_e2e(args.resume_e2e_report) if args.resume_e2e_report else None
        if args.db:
            report = measure(args.db, task=args.task, projection=args.projection)
        else:
            with TemporaryDirectory(prefix="hipson-agent-ux-") as directory:
                fixture = build(Path(directory))
                report = measure(fixture.db, task=args.task or fixture.heavy_task,
                                 ledger_source="fixture", projection=args.projection)
        report["resume_e2e"] = resume
        output = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
