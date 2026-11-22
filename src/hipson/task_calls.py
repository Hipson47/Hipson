"""Build runnable task calls for agent-facing reads, actions and refusals."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from typing import Any, TypedDict

CallValue = str | int | bool | float | list[str]


class ReadCall(TypedDict):
    tool: str
    arguments: dict[str, CallValue]
    cli: str


# Only codes the private verification command can emit cross this boundary.
VERIFIER_REFUSAL_CODES = frozenset({
    "input.text_invalid_unicode", "input.text_required", "ledger.database_unavailable", "ledger.path_unresolvable",
    "ledger.rollback_journal", "ledger.schema_unsupported", "ledger.snapshot_changed", "ledger.wal_pending",
    "project.unavailable", "run.launch_failed", "run.supervisor_failed", "task.not_found",
    "verify.argv_invalid", "verify.check_timeout",
    "verify.checks_undeclared", "verify.command_required", "verify.evidence_unavailable", "verify.head_moved",
    "verify.timeout_invalid", "verify.worker_context",
})


def valid_call(value: object, tools: set[str]) -> bool:
    if not isinstance(value, dict) or set(value) != {"tool", "arguments", "cli"}:
        return False
    tool, arguments, cli = value["tool"], value["arguments"], value["cli"]
    return (isinstance(tool, str) and tool in tools and isinstance(cli, str) and bool(cli.strip())
            and isinstance(arguments, dict) and all(
                isinstance(arg, (str, int, bool)) or (isinstance(arg, float) and isfinite(arg))
                or (isinstance(arg, list) and all(isinstance(item, str) for item in arg))
                for arg in arguments.values()))


def read_call(tool: str, arguments: Mapping[str, CallValue], cli_db: str | None, *,
              template: tuple[str, ...] = ()) -> ReadCall:
    names = {"task.findings": "task finding list", "task.finding_update": "task finding update"}
    command = ["hipson", *names.get(tool, tool.replace(".", " ")).split()]
    for key, value in arguments.items():
        if key in ("approved", "command") or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item.startswith("-"):
                command.append(f"{flag}={item}")
            else:
                command.append(flag)
                if item is not True:
                    command.append(str(item))
    if cli_db is not None:
        command.extend(["--db", cli_db])
    command.extend(("--json", *template))
    argv = arguments.get("command")
    if isinstance(argv, list):
        command.extend(("--", *argv))
    return {"tool": tool, "arguments": dict(arguments), "cli": shlex.join(command)}


def task_lookup_calls(db: Path) -> list[ReadCall]:
    return [read_call("task.list", {}, str(db)), read_call("task.now", {"detail": "compact"}, str(db))]


def project_fix_calls(task: str, db: Path) -> list[ReadCall]:
    return [read_call("task.show", {"task": task}, str(db)), read_call("task.run", {"help": True}, str(db))]


def verify_call(task: str, lead: str, db: Path, *, command: list[str] | None = None,
                timeout: float | None = None, explicit: bool = False) -> ReadCall:
    args: dict[str, CallValue] = {"task": task, "actor": lead, "approved": True}
    if command:
        args["command"] = command
    if timeout is not None:
        args["timeout"] = int(timeout) if int(timeout) == timeout else timeout
    template = ("--", "COMMAND") if explicit and not command else ()
    return read_call("task.verify", args, str(db), template=template)


def reviewer_call(task: str, db: Path) -> ReadCall:
    return read_call("task.run", {"task": task, "role": "reviewer", "mode": "ro"}, str(db),
                     template=("--actor", "REVIEWER", "--result-file", "RESULT_PATH", "--", "COMMAND"))


def gate_fix_calls(cases: list[tuple[str, str]], view: Mapping[str, Any], db: Path) -> list[ReadCall]:
    task, lead = view["id"], view["lead"]
    checks, latest = view["checks"], view["verification"]["latest"]
    retry = verify_call(task, lead, db, command=latest["command"] if latest and not checks else None,
                        explicit=not checks)
    fixes: list[ReadCall] = []
    for reason, code in cases:
        calls: list[ReadCall] = []
        if code == "close.checks_failed":
            calls.append(read_call("task.show", {"task": task, "section": "verification"}, str(db)))
            timeout = latest.get("timeout_seconds") if latest and reason == "failing verification: exit 124" else None
            calls.append(verify_call(task, lead, db, command=latest["command"] if not checks else None,
                                     timeout=min(86400, 2 * timeout)) if timeout else retry)
        elif code in {"close.checks_stale", "close.checks_missing", "verify.lead_missing", "verify.head_moved",
                      "verify.tree_changed"}:
            calls.append(retry)
        elif code == "close.review_required":
            if not view["verification"]["passing"]:
                calls.append(retry)
            calls.append(reviewer_call(task, db))
        elif code == "close.results_unacknowledged":
            assignment = reason.split()[2].rstrip(":")
            calls.extend((read_call("task.activity", {"assignment": assignment}, str(db)),
                          read_call("task.ack", {"assignment": assignment, "actor": lead, "approved": True}, str(db))))
        elif code == "close.findings_open":
            finding = reason.split()[3].rstrip(":")
            calls.extend((read_call("task.findings", {"task": task, "status": "open"}, str(db)),
                          read_call("task.finding_update", {"finding": finding, "actor": lead, "approved": True},
                                    str(db), template=("--status", "STATUS"))))
        elif code == "project.unavailable":
            calls.extend(project_fix_calls(task, db))
        fixes.extend(call for call in calls if call not in fixes)
    return fixes
