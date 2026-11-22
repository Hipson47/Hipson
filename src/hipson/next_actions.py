"""Rank lead actions using only compact projection inputs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from typing import Any, NotRequired

from hipson.ledger import Ledger
from hipson.task_calls import ReadCall, read_call
from hipson.task_projection import TERMINAL, ListProjection, TaskProjection

NEXT_ACTIONS_LIMIT = 10
WHY_LIMIT = 200
TEXT_SUMMARY_LIMIT = 160
PENDING_CHECK_STATES = ("missing", "partial", "unknown")
# Worker result fields the compact payload does not show; any content there means read before acking.
HIDDEN_RESULT_PARTS = ("files_changed", "commands", "findings", "risks")


class ActionCall(ReadCall):
    writes: bool


class NextAction(ActionCall):
    why: str
    then: NotRequired[ActionCall]


def _call(tool: str, arguments: dict[str, str | int | bool], cli_db: str | None, *,
          writes: bool = False, gated: bool = False) -> ActionCall:
    return {**read_call(tool, {**arguments, "approved": True} if gated else arguments, cli_db), "writes": writes}


def _action(why: str, call: ActionCall, *, then: ActionCall | None = None) -> NextAction:
    action: NextAction = {**call, "why": why if len(why) <= WHY_LIMIT else why[:WHY_LIMIT - 1] + "…"}
    if then is not None:
        action["then"] = then
    return action


def _check_action(row: dict[str, Any], task: TaskProjection, cli_db: str | None) -> NextAction | None:
    status = task["status"]
    # Never propose checks for work that has not started or is closed, and never verify a tree that a live
    # worker may still be editing: task.verify runs the declared commands and records lead evidence.
    if status == "open" or status in TERMINAL or row["has_active"]:
        return None
    counts: Counter[str] = Counter(check["recorded"] for check in task["checks"])
    stale = sum(check["current"] == "stale" for check in task["checks"])
    builder_finished = row["builder_finished_seq"] > row["latest_verification_seq"]
    args: dict[str, str | int | bool] = {"task": task["id"]}
    if counts["failing"]:
        call = _call("task.show", {**args, "section": "verification"}, cli_db)
    elif stale or ((status in ("verify", "review") or builder_finished)
                   and any(counts[state] for state in PENDING_CHECK_STATES)):
        call = _call("task.verify", args, cli_db, writes=True, gated=True)
    else:
        return None
    states = [("failing", counts["failing"]), ("stale", stale),
              *((state, counts[state]) for state in PENDING_CHECK_STATES)]
    detail = ", ".join(f"{count} {state}" for state, count in states if count)
    return _action(f"{task['id']} checks: {detail}.", call)


def escape_summary(summary: str) -> str:
    # The projection has already escaped display controls.
    return summary.replace("<", "&lt;").replace(">", "&gt;")


def _result_has_no_hidden_parts(result: str | None) -> bool:
    """True when a structured worker result adds nothing beyond the status and summary the payload shows."""
    if result is None:
        return True
    try:
        report = json.loads(result)
    except (TypeError, ValueError, RecursionError):
        return False
    return isinstance(report, dict) and not any(report.get(key) for key in HIDDEN_RESULT_PARTS)


def acknowledge_visible_results(store: Ledger, page: ListProjection, *, text: bool) -> None:
    actions = page["next_actions"]
    visible: dict[str, str] = {}
    for task in page["tasks"]:
        if "too_large" in task:
            continue
        for index, item in enumerate(task["assignments"]):
            summary = item["summary"]
            if (item["ack_state"] == "unacknowledged" and item["result_status"] and summary
                    and f"assignments.{index}.summary" not in task["truncated_fields"]
                    and (not text or len(escape_summary(summary)) <= TEXT_SUMMARY_LIMIT)):
                visible[item["id"]] = task["id"]
    for index, action in enumerate(actions):
        if action["tool"] != "task.activity":
            continue
        assignment = str(action["arguments"]["assignment"])
        if (task_id := visible.get(assignment)) is None:
            continue
        evidence = store.connection.execute(
            "SELECT activity IS NULL AND failure_journal IS NULL, result FROM assignments WHERE id = ?",
            (assignment,),
        ).fetchone()
        if evidence and evidence[0] and _result_has_no_hidden_parts(evidence[1]):
            actions[index] = _action(
                f"{task_id}/{assignment} result is shown in this payload under that task and assignment.", action["then"],
            )


def build(rows: Sequence[dict[str, Any]], tasks: Sequence[TaskProjection], *,
          cli_db: str | None = None) -> tuple[list[NextAction], int]:
    """Return the highest-priority actions and the count beyond the list limit."""
    results: list[NextAction] = []
    checks: list[NextAction] = []
    reviews: list[NextAction] = []
    findings: list[NextAction] = []
    stale_tasks: list[NextAction] = []  # Reserved for S5.
    cleanup: list[NextAction] = []
    for row, projected in sorted(zip(rows, tasks, strict=True), key=lambda pair: pair[1]["id"]):
        task = projected["id"]
        args = {"task": task}
        unread = sorted(item["id"] for item in row["assignments"] if item["ack_state"] == "unacknowledged")
        for assignment in unread:
            assigned = {"assignment": assignment}
            results.append(_action(
                f"{task}/{assignment} has an unacknowledged result. Read it before acknowledging.",
                _call("task.activity", assigned, cli_db),
                then=_call("task.ack", assigned, cli_db, writes=True, gated=True),
            ))

        status = projected["status"]
        severe = sorted((item for item in row["findings"] if item["severity"] in ("blocking", "major")),
                        key=lambda item: item["id"])
        if check_action := _check_action(row, projected, cli_db):
            checks.append(check_action)
        if status == "review":
            recorded_ok = (bool(projected["checks"]) and "checks" not in projected["truncated_fields"] and
                           all(check["recorded"] == "ok" and check["current"] != "stale" for check in projected["checks"]))
            review_ok = projected["review_policy"] != "required" or projected["review"]["state"] == "recorded_snapshot"
            if recorded_ok and review_ok and not (unread or severe or row["has_active"]) and "\x00" not in row["lead"]:
                reviews.append(_action(f"{task} in review: recorded ok, not re-checked.",
                                       _call("task.status", {**args, "to": "done", "actor": row["lead"]},
                                             cli_db, writes=True, gated=True)))
            else:
                reviews.append(_action(f"{task} in review needs inspection.",
                                       _call("task.show", {**args, "detail": "compact"}, cli_db)))
        for severity in dict.fromkeys(item["severity"] for item in severe):
            count = projected["open_findings"][severity]
            findings.append(_action(f"{task} has {count} open {severity} {'finding' if count == 1 else 'findings'}.",
                                     _call("task.findings", {**args, "status": "open", "severity": severity}, cli_db)))

        # Claims are created with active assignments and released in the same
        # transaction that ends them. The projection exposes the active signal.
        if status in TERMINAL and row["has_active"]:
            cleanup.append(_action(f"{task} is {status} with active assignments.",
                                   _call("task.reap", {**args, "dry_run": True}, cli_db, gated=True)))

    actions = results + checks + reviews + findings + stale_tasks + cleanup
    selected = actions[:NEXT_ACTIONS_LIMIT]
    return selected, len(actions) - len(selected)
