# Hipson 1.4.0

Hipson 1.4.0 gives the lead agent eyes on running work and a way back after losing context. It
was driven by the lead's own friction and by an evidence review of lead/worker agent failures
(see [docs/ROADMAP.md](ROADMAP.md)).

Status: **beta**.

## Highlights

- **Assignment activity** (`hipson task activity`): `hipson task run` records heartbeats, the last
  output line and progress lines a worker appends to `$HIPSON_PROGRESS_FILE` (no ledger access
  needed). Observations are bounded, redacted (including UTF-16 output and overlapping secrets)
  and always treated as untrusted. Stale activity never releases claims.
- **Failure journal**: when a worker ends without a valid result, the ledger keeps the exit status,
  duration, changed files, `git diff --stat`, a log tail and an explicit `result_state`, instead of
  losing the evidence or inventing an outcome.
- **Result acknowledgement** (`hipson task ack`, MCP `task.ack`): a delivered result or failure
  journal stays visible until the lead acknowledges it, and blocks `done` until then (audited
  override as before). Results that predate the feature are marked legacy.
- **`hipson task now`**: one compact view of what needs the lead: unacknowledged results, blockers,
  active work with last activity, tasks awaiting review, cleanup.
- **`hipson lead resume`**: deterministic, read-only, size-budgeted state for a lead after context
  loss: open tasks, pending results, blockers, held scopes, recent decisions, verification freshness
  and git state. It creates no files (no WAL/SHM sidecars) and runs git without hooks, pagers or
  content filters.
- **Lead quick reference** in `hipson --help`, `hipson task --help`, README and docs.

## Measured

New agent e2e scenarios (worker dies without a result, a result arrives while the lead is busy, the
lead resumes after losing context) were run with a real Codex lead (gpt-6.1-sol): 6/6 passed after
the scenario oracles were corrected to judge outcomes instead of exact commands (a replay of the
earlier runs showed they had reached the right outcome too). After the quick reference was added,
agents opened with `hipson task now` or used `hipson lead resume` in most runs; earlier they rebuilt
state from `task show`. The sample is small; treat it as a smoke signal, not a benchmark.

On the maintainer's real 21-task ledger, `hipson lead resume` is about 3.9 KB with open work first.

## Upgrade notes

- The ledger migrates to schema v5 on the first write; reads never migrate.
- A task with a delivered, unacknowledged result can no longer be closed without `hipson task ack`
  (or an explicit `--force --reason`). The README quick start shows the new step.
