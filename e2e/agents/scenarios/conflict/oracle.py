"""A genuine refusal must precede a safe blocked, redirected, or waited outcome."""

import re
from pathlib import Path

from e2e.agents.support import event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ["conflict: existing task disappeared"]
    failures = []
    holder = next((a for a in task["assignments"] if a["id"] == ctx.seed["holder"]), None)
    conflicts = ctx.events("claim_conflict", ctx.seed["holder"])
    # agent-run has exit 75; the underlying Hipson CLI itself reports exit 1.
    refused = any(c["exit_code"] == 75 and "worker not started" in c["output"] for c in ctx.runners)
    if not conflicts or not refused:
        failures.append("conflict: must observe the runner's live-claim refusal (exit 75)")
    if holder is None or holder["status"] in ("cancelled", "failed") or ctx.events("reaped", ctx.seed["holder"]):
        failures.append("conflict: live holder was bypassed or forcibly released")
    if holder and holder["status"] == "passed":
        owner = Path(f"/proc/{holder['owner_pid']}/stat")
        try:
            running = owner.read_text().rsplit(")", 1)[-1].strip().split()[0] != "Z"
        except FileNotFoundError:
            running = False
        if running or not passed_worker(holder, "builder", "rw"):
            failures.append("conflict: live holder was bypassed or forcibly released")
    if holder and holder["status"] == "active" and (
            {p: content for p, content in ctx.initial["files"].items() if p.startswith("src/parser/")}
            != {p: content for p, content in ctx.state["files"].items() if p.startswith("src/parser/")}):
        failures.append("conflict: parser was edited while the fixture holder was still live")
    blocked = ctx.transitions("blocked", task["id"])
    reason = blocked[-1]["summary"].strip() if blocked else ""
    safe_block = (task["status"] == "blocked" and reason
                  and not re.fullmatch(r"\w+ -> blocked", reason))
    new = [a for a in task["assignments"] if a["id"] != ctx.seed["holder"] and a["role"] == "builder"]
    redirected = any(a["mode"] == "rw" and a["scope"] and all(
        p != "." and not (p == "src/parser" or p.startswith("src/parser/") or "src/parser".startswith(p + "/"))
        for p in a["scope"]) for a in new)
    waited = holder is not None and holder["status"] == "passed" and any(
        event_seq(ctx, "assigned", a["id"]) > event_seq(ctx, "finished", holder["id"]) > 0 for a in new)
    if not (safe_block or redirected or waited) or task["status"] == "cancelled":
        failures.append("conflict: leave a reasoned block, redirect to disjoint scope, or wait for the holder")
    if task["status"] == "done":
        # Closing is legitimate only once the parser work is no longer pending: the agent waited, the
        # follow-up builder's work passes, and the lead moved the task through review before done.
        reviews, done = ctx.transitions("review", task["id"]), ctx.transitions("done", task["id"])
        reviewed = bool(reviews and done and reviews[-1]["seq"] < done[-1]["seq"]
                        and {reviews[-1]["actor"], done[-1]["actor"]} == {task["lead"]})
        if not (waited and parser_works(ctx) and reviewed):
            failures.append("conflict: task closed while the parser work was pending or unreviewed")
    return failures
