"""Build runnable task calls for agent-facing reads, actions and refusals."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TypedDict


class ReadCall(TypedDict):
    tool: str
    arguments: dict[str, str | int | bool]
    cli: str


def read_call(tool: str, arguments: dict[str, str | int | bool], cli_db: str | None) -> ReadCall:
    command = ["hipson", *("task finding list".split() if tool == "task.findings" else tool.split("."))]
    for key, value in arguments.items():
        if key == "approved":
            continue  # MCP approval is not a CLI argument.
        key = key.replace("_", "-")
        if isinstance(value, str) and value.startswith("-"):
            command.append(f"--{key}={value}")
            continue
        command.append(f"--{key}")
        if value is not True:
            command.append(str(value))
    if cli_db is not None:
        command.extend(["--db", cli_db])
    return {"tool": tool, "arguments": arguments, "cli": shlex.join([*command, "--json"])}


def task_lookup_calls(db: Path) -> list[ReadCall]:
    return [read_call("task.list", {}, str(db)), read_call("task.now", {"detail": "compact"}, str(db))]
