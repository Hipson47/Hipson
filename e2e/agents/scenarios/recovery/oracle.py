"""Require recovery evidence before a replacement's safe parser assignment."""

from e2e.agents.support import event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ["recovery: existing task disappeared"]
    failures = []
    stale = next((a for a in task["assignments"] if a["id"] == ctx.seed["stale"]), None)
    reaped = ctx.events("reaped", ctx.seed["stale"])
    if stale is None or stale["status"] != "cancelled" or not reaped or "pid_gone" not in reaped[0]["summary"]:
        failures.append("recovery: stale claim must be released by ledger reap (pid_gone)")
    replacements = [a for a in task["assignments"] if a["id"] != ctx.seed["stale"]
                    and passed_worker(a, "builder", "rw") and "src/parser" in a["scope"]]
    if not replacements or not reaped or not any(
            event_seq(ctx, "assigned", a["id"]) > reaped[0]["seq"] for a in replacements):
        failures.append("recovery: replacement builder must be assigned and finish after reap")
    if task["claims"]:
        failures.append("recovery: finished parser work still holds claims")
    if task["status"] != "in_progress":
        failures.append("recovery: leave recovered task in progress for review")
    if not parser_works(ctx):
        failures.append("recovery: replacement did not fix colon parsing")
    return failures
