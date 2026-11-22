"""Build runnable task calls for agent-facing reads, actions and refusals."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from contextlib import closing
from itertools import chain, groupby
from math import isfinite
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from hipson.ledger import LedgerError

CallValue = str | int | bool | float | list[str]
DEFAULT_STALE_DAYS = 7
MIN_STALE_DAYS = 1
MAX_STALE_DAYS = 3650


class ReadCall(TypedDict):
    tool: str
    arguments: dict[str, CallValue]
    cli: str


class CleanupAssignment(TypedDict):
    id: str
    role: str
    mode: str
    status: str
    claims: list[str]


class CleanupTask(TypedDict):
    id: str
    project: str
    status: str
    assignments: list[CleanupAssignment]
    omitted_assignments: int
    omitted_claims: int


class CleanupReport(TypedDict):
    project: str
    tasks: list[CleanupTask]
    omitted_tasks: int


# A path such as / or a home directory can match many tasks: keep the report and the fix list bounded.
CLEANUP_TASK_LIMIT = 50
CLEANUP_ASSIGNMENT_LIMIT = 20
CLEANUP_CLAIM_LIMIT = 20
CLEANUP_FIX_LIMIT = 30


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


REPLAY_ARG_LIMIT = 4096  # task_verification stores each argument through safe_text(arg, 4096).
# safe_text stores these in place of a whole argument it could not keep.
STORED_PLACEHOLDERS = ("[unsupported encoded observation]", "[observation exceeded size limit]")


def task_lookup_calls(db: Path) -> list[ReadCall]:
    return [read_call("task.list", {}, str(db)), read_call("task.now", {"detail": "compact"}, str(db))]


def project_fix_calls(task: str, db: Path) -> list[ReadCall]:
    return [read_call("task.show", {"task": task}, str(db)), read_call("task.run", {"help": True}, str(db))]


def cleanup_project(db: Path, project: str) -> tuple[CleanupReport, LedgerError | None]:
    """Read the tasks and claims registered at or below a path in one ledger snapshot."""
    from hipson.ledger import TERMINAL_STATUSES, Ledger, LedgerError, _same_project, require_text
    from hipson.redaction import redact_value

    require_text(project, "Cleanup project path")
    root = str(Path(project).expanduser().resolve())
    report = CleanupReport(project=root, tasks=[], omitted_tasks=0)
    if not db.exists():
        return redact_value(report), None

    reads: list[ReadCall] = []
    templates: list[ReadCall] = []
    nonterminal = active = read_count = 0
    cli_db = str(db)
    with closing(Ledger(db, create=False, read_only=True, sidecar_free=True)) as store, store._transaction(write=False):
        for task in store.connection.execute("SELECT id, project, status, lead FROM tasks ORDER BY id"):
            registered = Path(task["project"])
            if not any(_same_project(str(parent), root) for parent in (registered, *registered.parents)):
                continue
            shown = len(report["tasks"]) < CLEANUP_TASK_LIMIT
            assignments: list[CleanupAssignment] = []
            active_ids: list[str] = []
            assignment_count = active_count = claims_shown = omitted_claims = 0
            items = store.connection.execute(
                """SELECT a.id, a.role, a.mode, a.status, c.path
                   FROM assignments AS a LEFT JOIN claims AS c ON c.assignment = a.id
                   WHERE a.task = ? ORDER BY (a.status = 'active') DESC, a.id, c.path""",
                (task["id"],),
            )
            for assignment_id, rows in groupby(items, key=itemgetter("id")):
                first = next(rows)
                assignment_count += 1
                if first["status"] == "active":
                    active_count += 1
                    if len(reads) + len(active_ids) < CLEANUP_FIX_LIMIT:
                        active_ids.append(assignment_id)
                if not shown:
                    continue
                show_assignment = len(assignments) < CLEANUP_ASSIGNMENT_LIMIT
                claims: list[str] = []
                for row in chain((first,), rows):
                    if row["path"] is None:
                        continue
                    if show_assignment and claims_shown < CLEANUP_CLAIM_LIMIT:
                        claims.append(row["path"])
                        claims_shown += 1
                    else:
                        omitted_claims += 1
                if show_assignment:
                    assignments.append(CleanupAssignment(
                        id=assignment_id, role=first["role"], mode=first["mode"], status=first["status"],
                        claims=claims))
            if shown:
                report["tasks"].append(CleanupTask(
                    id=task["id"], project=task["project"], status=task["status"],
                    assignments=assignments, omitted_assignments=assignment_count - len(assignments),
                    omitted_claims=omitted_claims))
            else:
                report["omitted_tasks"] += 1

            is_open = task["status"] not in TERMINAL_STATUSES
            if is_open or active_count:
                read_count += 1 + active_count
                if len(reads) < CLEANUP_FIX_LIMIT:
                    reads.append(read_call("task.show", {"task": task["id"], "detail": "compact"}, cli_db))
                    reads.extend(read_call("task.activity", {"assignment": item}, cli_db)
                                 for item in active_ids[:CLEANUP_FIX_LIMIT - len(reads)])
            if is_open and len(templates) < CLEANUP_FIX_LIMIT:
                args = {"task": task["id"]}
                templates.extend((
                    read_call("task.close", args, cli_db),
                    read_call("task.status", {**args, "to": "cancelled", "actor": task["lead"]}, cli_db,
                              template=("--summary", "REASON")),
                ))
            nonterminal += is_open
            active += active_count

    # Leave room for a close and cancel pair whenever there is a nonterminal task.
    fix = reads[:CLEANUP_FIX_LIMIT - min(2, len(templates))]
    fix.extend(templates[:CLEANUP_FIX_LIMIT - len(fix)])
    omitted_fix = read_count + 2 * nonterminal - len(fix)
    refusal = LedgerError(
        f"Cleanup refused: {nonterminal} nonterminal task(s), {active} active assignment(s)."
        + (f" {omitted_fix} more suggested call(s) omitted; narrow the path or read task now." if omitted_fix else ""),
        code="cleanup.tasks_open", fix=fix,
    ) if nonterminal or active else None
    return redact_value(report), refusal


def replayable(command: list[str] | None) -> list[str] | None:
    """Replay only an argv that every display and storage path keeps byte for byte.

    Verification stores each argument through safe_text (redaction, environment secrets, whole-argument
    placeholders, a 4,096-character cut) and error bodies redact fix calls again, so an argument any of them
    would change is not the argv that ran.
    """
    from hipson.redaction import redact_text
    from hipson.task_activity import safe_text

    if not command or any("[REDACTED]" in arg or len(arg) >= REPLAY_ARG_LIMIT or arg in STORED_PLACEHOLDERS
                          or redact_text(arg) != arg or safe_text(arg, REPLAY_ARG_LIMIT) != arg for arg in command):
        return None
    return command


def verify_call(task: str, lead: str, db: Path, *, command: list[str] | None = None,
                timeout: float | None = None, explicit: bool = False) -> ReadCall:
    from hipson.redaction import redact_value

    explicit = explicit or command is not None
    command = replayable(command)
    args: dict[str, CallValue] = {"task": task, "actor": lead, "approved": True}
    if timeout is not None:
        args["timeout"] = int(timeout) if int(timeout) == timeout else timeout
    if command:
        call = read_call("task.verify", {**args, "command": command}, str(db))
        # Error bodies redact fix calls as a whole, joined cli line included, so a secret split across
        # arguments ("Bearer" "TOKEN", "token" "=" "VALUE") changes the call: then it is not a replay.
        if redact_value(call) == call:
            return call
    return read_call("task.verify", args, str(db), template=("--", "COMMAND") if explicit else ())


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
                                     timeout=min(86400, 2 * timeout), explicit=not checks) if timeout else retry)
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
