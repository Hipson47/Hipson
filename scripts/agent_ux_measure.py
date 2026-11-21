#!/usr/bin/env python3
"""Measure Hipson's CLI and MCP read surfaces offline."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
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

        def inventory_size(task: ledger.Task) -> int:
            records = store.verifications(task["id"])
            return len(records[-1].get("files", [])) if records else 0

        return max(live or tasks, key=inventory_size)["id"]


def measure(db: Path, *, task: str | None = None, ledger_source: str = "provided") -> dict[str, Any]:
    db = db.expanduser().resolve(strict=True)
    task = task or _select_task(db)
    revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    with patch.dict(os.environ, HIPSON_LEDGER=str(db)):
        detail_tools = {tool["name"] for tool in _mcp("tools/list", {})["tools"]
                        if "detail" in tool["inputSchema"].get("properties", {})}
        reads = {name: _measure_read(name, task, mcp_detail=name in detail_tools) for name in READS}
    return {"schema_version": 1, "hipson_version": __version__, "git_revision": revision,
            "ledger_source": ledger_source, "task_show_id": task, "reads": reads}


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
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path (default: stdout)")
    parser.add_argument("--resume-e2e-report", type=Path, help="Include metrics from agent_e2e.py's report.json")
    args = parser.parse_args(argv)
    try:
        resume = _resume_e2e(args.resume_e2e_report) if args.resume_e2e_report else None
        if args.db:
            report = measure(args.db, task=args.task)
        else:
            with TemporaryDirectory(prefix="hipson-agent-ux-") as directory:
                fixture = build(Path(directory))
                report = measure(fixture.db, task=args.task or fixture.heavy_task, ledger_source="fixture")
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
