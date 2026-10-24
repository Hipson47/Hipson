# Task ledger (S1)

Claude Code is the only orchestrator. The lead (or its worker wrappers acting on
its behalf) writes tasks, assignments, and events for Codex/agy workers. Workers
never write the ledger themselves. `actor` and `lead` are recorded labels, **not
authenticated identities**. This CLI does not enforce that operational boundary.

The database defaults to `<detect_hipson_home()>/ledger.sqlite`. `HIPSON_LEDGER`
overrides the default; `--db PATH` overrides both. Every subcommand supports
`--db` and `--json` (also accepted before the subcommand). No provider, Hermes,
MCP integration, or worker execution is involved.

```sh
hipson task open --project . --title "Add ledger" --objective "Track worker ownership" \
  --acceptance "Claims cannot overlap" --acceptance "Tests pass" --lead claude --json
hipson task assign --task T-0001 --actor codex --role builder --mode rw \
  --scope src/hipson --scope tests/test_ledger.py --json
hipson task event --task T-0001 --actor codex --type progress --summary "Implementation ready"
hipson task finish --assignment A-0001 --result passed --summary "Checks pass" --ref tests/test_ledger.py
hipson task status --task T-0001 --to review --actor claude
hipson task status --task T-0001 --to done --actor claude --summary "Review accepted"
hipson task show --task T-0001 --json
hipson task events --task T-0001 --after 2 --json
hipson task list --project . --status done --json
hipson task board
```

Repeat `--acceptance`, `--scope`, and `--ref` for multiple values. Roles are
`builder|reviewer|explorer|researcher`; modes are `ro|rw`. Assignment results are
`passed|failed|cancelled`. Task and assignment IDs are database-wide sequential
`T-0001` / `A-0001` identifiers (growing beyond four digits when necessary).
Project paths are expanded and resolved to absolute paths.

Allowed task transitions:

```text
open -> in_progress
in_progress <-> blocked
in_progress <-> review
review -> done
any non-terminal -> cancelled
```

Assignment to an open task advances it to `in_progress` in the same transaction.
Assignments can also be recorded while blocked or in review. Terminal tasks
cannot transition or receive new assignments. Task status changes do not finish
assignments: finish each active assignment explicitly to release its claims,
including after task cancellation. Notes/events and finishing existing active
assignments remain available after task termination for audit and cleanup.
Finishing an assignment twice fails. The board omits terminal tasks and shows
only active assignments; `show` includes the full assignment history and current
claims. List and board JSON return arrays; task/event JSON use the registered
`hipson.task` / `hipson.task_event` schemas.

Write assignments claim each normalized scope path until finished. Paths use
repo-relative POSIX syntax; backslashes, redundant separators and `.` components
are normalized, and duplicates removed. Absolute paths, drive prefixes, NULs,
and any `..` component are rejected. `.` claims the entire project. Equal paths
or ancestor/descendant paths conflict across all tasks in the same resolved
project; disjoint paths and other projects do not. Read assignments take no
claims. An `rw` assignment requires at least one scope path (use `.` for the
whole project), so a writer is never unclaimed. Matching is lexical,
case-sensitive, and does not resolve scope symlinks or coordinate nested project
roots; claims are coordination records, not filesystem access controls.

SQLite uses WAL, foreign keys, a 10-second busy timeout, and a version table
(database version 1; artifact schema version `1.0`). Initialization and each
mutation use `BEGIN IMMEDIATE`. Each mutation commits its state change, event,
UTC ISO timestamps, and task revision together; an opened task starts at revision
1. A conflicting assignment commits only a `claim_conflict` event and the
associated task revision/timestamp update, then fails with the holder assignment
ID. It leaves status, assignments, and claims unchanged. Other failed mutations
roll back completely. Events have a database-wide autoincrement sequence;
`--after N` is exclusive and task events are returned in ascending sequence order.
Read snapshots do not increment revisions. SQLite triggers reject updates and
deletes to events; this is append-only history, not tamper-proof storage against
someone who controls the database file.

The existing `redaction.redact_text` runs on titles, objectives, acceptance items,
and all event summaries **before persistence**. Actor labels, project/scope paths,
event types, and refs are structural data and are not redacted: put credentials
in none of these fields. Generic event types (such as `note`, `progress`,
`blocked`, or `result`) are labels only; events do not change lifecycle state.
Use `status` for transitions and `assign`/`finish` for assignment state.

CLI success returns 0; operation, conflict, and storage errors return 1 with a
message on stderr; argument errors follow argparse (2). There are no automatic
retries after a successful commit: inspect the ledger before replaying an
operation whose response was lost. S2–S4 can add wrapper integration, recovery
and idempotency policies, and richer orchestration without making workers writers.
