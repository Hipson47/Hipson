"""Require the builder → reviewer finding → fix → lead review/close lifecycle."""

from e2e.agents.support import event_seq, parser_works, passed_worker


def check(ctx):
    task = ctx.task()
    if task is None:
        return ["lead-flow: parser task was not opened"]
    failures = []
    builders = [a for a in task["assignments"] if passed_worker(a, "builder", "rw")]
    reviewers = [a for a in task["assignments"] if passed_worker(a, "reviewer", "ro")]
    findings = [f for f in ctx.state["findings"] if f["task"] == task["id"]]
    if len(builders) < 2 or not reviewers or len(findings) != 1:
        failures.append("lead-flow: requires a builder, separate structured reviewer with one finding, and fix builder")
    else:
        build, fix, review, finding = builders[0], builders[-1], reviewers[0], findings[0]
        parser = "src/parser/pairs.py"
        if any(not any(p == "." or parser == p or parser.startswith(p + "/") for p in a["scope"])
               for a in (build, fix)):
            failures.append("lead-flow: builders must hold claims covering src/parser/pairs.py")
        if len({task["lead"], build["actor"], review["actor"]}) != 3 or fix["actor"] == review["actor"]:
            failures.append("lead-flow: lead, builder, and reviewer must be separate actors")
        if (finding["source_assignment"] != review["id"] or finding["status"] != "fixed"
                or finding["resolved_by_assignment"] != fix["id"] or not finding["resolution_note"]
                or len(review["result"]["findings"]) != 1):
            failures.append("lead-flow: reviewer finding must be fixed and linked to the follow-up assignment")
        ordered = [event_seq(ctx, "opened", None), event_seq(ctx, "assigned", build["id"]),
                   event_seq(ctx, "finished", build["id"]), event_seq(ctx, "assigned", review["id"]),
                   # Finding creation precedes reviewer finish inside the same atomic transaction.
                   event_seq(ctx, "finding_opened", finding["id"]), event_seq(ctx, "finished", review["id"]),
                   event_seq(ctx, "assigned", fix["id"]), event_seq(ctx, "finished", fix["id"]),
                   event_seq(ctx, "finding_resolved", finding["id"])]
        reviews, done = ctx.transitions("review", task["id"]), ctx.transitions("done", task["id"])
        ordered += [reviews[-1]["seq"] if reviews else -1, done[-1]["seq"] if done else -1]
        if -1 in ordered or any(a >= b for a, b in zip(ordered, ordered[1:], strict=False)):
            failures.append("lead-flow: builder/reviewer/fix/review/done event order is incomplete or wrong")
        for event in [*reviews, *done, *ctx.events("finding_resolved", finding["id"])]:
            if event["actor"] != task["lead"]:
                failures.append("lead-flow: only the lead may resolve, review, and close the task")
    if task["status"] != "done":
        failures.append("lead-flow: task is not done")
    if task["claims"] or any(a["status"] == "active" for a in task["assignments"]):
        failures.append("lead-flow: worker claims/assignments were not released")
    if not parser_works(ctx, trimmed=True):
        failures.append("lead-flow: parser acceptance failed (colon splitting and whitespace trimming)")
    return failures
