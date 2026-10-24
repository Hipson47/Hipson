# Task ledger

Claude Code is the only orchestrator. The lead (or its worker wrappers acting on
its behalf) writes tasks, assignments, and events for Codex/agy workers. Workers
never write the ledger themselves. `actor` and `lead` are recorded labels, **not
authenticated identities**. This CLI does not enforce that operational boundary.

The database defaults to `<detect_hipson_home()>/ledger.sqlite`. `HIPSON_LEDGER`
overrides the default; `--db PATH` overrides both. Every subcommand supports
`--db` and `--json` (also accepted before the subcommand). The CLI and MCP tools
use the same central ledger; no provider, Hermes, or worker execution is involved.

## Lead and worker flow

1. The lead opens a task with its project, objective, acceptance criteria, and
   lead label using `hipson task open`.
2. A lead-controlled wrapper records `hipson task assign` before launching the
   worker. It supplies the worker's actor/role/mode, explicit `--scope` paths for
   `rw` claims, and its own `--owner-pid`, `--owner-boot-id`, and `--owner-host`
   (optionally `--lease-seconds`). The wrapper establishes its process group as
   described under [Orphan recovery](#orphan-recovery-s3a). Assignment atomically
   claims the paths and advances an open task to `in_progress`.
3. The worker runs its bounded brief and returns changes and verification
   evidence. It does not spawn agents, write the ledger, commit/push, or create
   progress/handoff notes. The wrapper or lead records any progress events.
4. The wrapper calls `hipson task finish` with the assignment ID, result,
   summary, and evidence refs. This ends the assignment and releases its claims;
   it does not complete the task or independently verify the worker's report.
5. The lead inspects the diff and verifies acceptance criteria, then uses
   `hipson task status` to move the task to `review` and, once accepted, `done`.
   Failed work can return from review to `in_progress` for another assignment.
6. For an interrupted wrapper, inspect `hipson task reap --dry-run`, then run
   `hipson task reap` to cancel proven orphans and release their claims. Reaping
   does not change task status; the lead decides how to resume or close it.

The wrapper is an external integration owned by the lead, not a worker launcher
provided by `hipson task`. A finished assignment or a `passed` result is a
recorded outcome, not an enforced verification gate on task status.

## CLI examples

These commands are issued by the lead or its wrapper on the worker's behalf:

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
hipson task reap --dry-run --json
hipson task reap --task T-0001 --json
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
(database version 2; artifact schema version `1.0`). Existing version 1 stores
migrate in place in one transaction: four nullable assignment columns are added
and the version is advanced to 2. All existing rows, claims, and events remain;
old assignments receive NULL ownership/lease values. Unknown versions are
refused. Initialization and each
mutation use `BEGIN IMMEDIATE`. Each mutation commits its state change, event,
UTC ISO timestamps, and task revision together; an opened task starts at revision
1. Before an `rw` conflict check, overlapping orphaned holders in the same project
are reaped in the assignment's transaction. A remaining conflict commits a
`claim_conflict` event and any recovery already performed, then fails with the
live holder assignment ID; no new assignment or claims are created. Without
orphans, the existing assignments and claims stay unchanged. Other failed
mutations roll back completely. Events have a database-wide autoincrement sequence;
`--after N` is exclusive and task events are returned in ascending sequence order.
Read snapshots do not increment revisions. SQLite triggers reject updates and
deletes to events; this is append-only history, not tamper-proof storage against
someone who controls the database file.

The existing `redaction.redact_text` runs on titles, objectives, acceptance items,
and all event summaries **before persistence**. Actor labels, project/scope paths,
event types, and refs are structural data and are not redacted: put credentials
in none of these fields. Generic event types (such as `note`, `progress`,
`blocked`, or `result`) are labels only; events do not change lifecycle state.
Use `status` for transitions and `assign`/`finish`/`reap` for assignment state.

CLI success returns 0; operation, conflict, and storage errors return 1 with a
message on stderr; argument errors follow argparse (2). There are no automatic
retries after a successful commit: inspect the ledger before replaying an
operation whose response was lost. Later stages can add built-in worker wrappers,
idempotency policies, and richer orchestration without making workers writers.

## Orphan recovery (S3a)

An optional owner identifies the wrapper process responsible for finishing an
assignment. The wrapper must call `setsid` to become its process group's leader
(PID equals PGID), then start the worker as a child in that group. Supply the
wrapper's positive PID, Linux boot ID, and hostname when assigning:

```sh
hipson task assign --task T-0001 --actor codex --role builder --mode rw --scope src \
  --owner-pid "$$" --owner-boot-id "$(cat /proc/sys/kernel/random/boot_id)" \
  --owner-host "$(hostname)" --lease-seconds 3600 --json
```

`owner_pid`, `owner_boot_id`, `owner_host`, and `lease_until` appear in assignment
JSON as nullable fields. All inputs are optional and no owner is inferred from
the CLI or MCP server process. `--lease-seconds` is a nonnegative integer; it
sets a UTC expiry relative to assignment creation (`0` expires immediately).
There is no lease renewal operation in S3a.

Only active assignments are considered. Recovery chooses the first applicable
reason in this order:

| Reason | Condition |
| --- | --- |
| `lease_expired` | The lease has expired, regardless of host or process liveness. |
| `boot_id_changed` | The recorded host equals the current hostname, and the supplied boot ID differs from the current Linux boot ID. |
| `pid_gone` | The recorded host equals the current hostname, and both the supplied PID and its process group no longer exist. |

Owner checks use `os.kill(pid, 0)` and, if the PID is absent,
`os.killpg(pid, 0)`. Only ESRCH from both proves the owner gone; EPERM or any
other probe error means alive. A surviving worker holds the claim after its
runner is killed. Zombies also count as present until their parent reaps them.
Missing boot/PID fields cannot prove
that condition; an unavailable current boot ID cannot prove a boot change. A
different or missing host disables liveness checks. An owner-less assignment is
only recoverable through an expired lease; an assignment on another host with
no lease remains active.

`hipson task reap [--task T] [--dry-run]` examines all active assignments, including
those on terminal tasks. Each orphan is rechecked under the writer lock, marked
`cancelled`, given `ended_at`, and has all claims removed. One `reaped` event
records its assignment ID and reason, advancing the task revision; this commits
in one transaction per orphan. Reaping does not change task status. Earlier
orphans remain committed if a later orphan fails. Repeating a reap is safe.

CLI JSON and MCP return an array of `{"assignment": <assignment>, "reason": <code>}`.
Dry runs return the existing active assignment rather than a projected cancelled
row. They write no assignment, claim, event, revision, or migration, and do not
create missing stores or directories. A dry run on v1 can inspect legacy rows
without upgrading the database. An unknown task filter fails with `Task not found`.

An `rw` assign automatically recovers only holders with overlapping claims in the
same resolved project before checking for conflicts, in its existing transaction.
It releases every claim of each recovered holder and appends `reaped` before
`assigned`. Disjoint holders, other projects, and readers are left to explicit
reaping. A live overlapping holder still causes `claim_conflict`.

The Python `Ledger` constructor accepts `hostname`, `boot_id`, and `pid_alive`
callables so tests can control liveness without depending on free real PIDs.
The injected `pid_alive` probe represents both PID and process-group liveness.
PID or process-group ID reuse within one boot cannot be detected with this ownership tuple, so it
can delay recovery; use a bounded lease when that matters. Lease expiry releases
claims even if the owner is alive, so wrappers must choose durations accordingly.

## Read-only access

CLI `task show/events/list/board`, `task reap --dry-run`, and MCP task reads open
the ledger read-only. They never initialize a database, migrate v1 to v2, or set
WAL journal mode. V1 assignments expose null owner fields without changing the
file. Writes still initialize and migrate transactionally.

Missing stores return empty arrays for list/board and unfiltered dry runs;
show/events and task-filtered dry runs return `Task not found`. Plain CLI board
prints `No active tasks.`. No missing file or parent directory is created.

SQLite's ordinary `mode=ro` can still create WAL/SHM sidecars. To avoid that for
checkpointed stores, reads copy a journal-free database into an in-memory,
query-only snapshot. An immutable source connection is used only for this copy;
the source identity, size, modification/change times, and absence of WAL/journal
files are rechecked before exposing it. Changes discard the copy and retry up to
three times. This also works for a closed v2 WAL-mode store in a read-only
directory, without creating sidecars. Snapshot memory scales with database size.

A live WAL store is opened with `mode=ro&readonly_shm=1`, using existing readable
WAL and SHM files; committed WAL rows remain visible in read-only directories.
On Linux/WSL, a temporary shared OFD lock pins those files until SQLite acquires
its own database read lock, so a writer closing during open cannot remove them
and cause the reader to recreate them. Systems without Linux OFD locking fail
safely for live WAL reads; checkpoint and close the store to use a snapshot.
A WAL without SHM fails with an actionable error instead of creating SHM,
reopening writable, or ignoring WAL through immutable mode. Have a writable
owner initialize the sidecars, or checkpoint and close the store before retrying.
An existing rollback journal also requires its writable owner to finish recovery.
SQLite recovery/locking errors propagate safely without a writable retry.
See [SQLite's read-only WAL requirements](https://www.sqlite.org/wal.html#read_only_databases).

## MCP stdio (S2)

Claude Code can drive the ledger directly through the existing MCP server. From
Windows, start the WSL-installed CLI with the Linux project path:

```sh
wsl.exe -d Ubuntu -- hipson mcp serve --project /home/hipson47/code/Hipson --stdio
```

Ensure `hipson` is on the WSL process's PATH (or use its absolute installed path).
The server's `--project` configures existing project tools; it does **not** select
the ledger database, filter the task board/list, or supply `task.open.project`.
Task tools use only the trusted server environment to select the store:
`HIPSON_LEDGER` overrides the default `HIPSON_HOME/ledger.sqlite` (or the
detected Hipson home when unset). MCP arguments cannot select a database;
`db` is rejected. The CLI's `--db` option remains available. Relative paths
resolve against the server's working directory, as in the CLI.

Read tools use the read-only access rules above. With no store,
`task.list` and `task.board` return empty arrays; `task.show` and `task.events`
return `Task not found` errors. Approved write tools may initialize the store.

| Tool | Required arguments | Optional arguments |
| --- | --- | --- |
| `task.show` | `task` | none |
| `task.events` | `task` | `after` (exclusive, default `0`) |
| `task.list` | none | `project`, `status` |
| `task.board` | none | none |
| `task.open` | `project`, `title`, `objective`, `approved=true` | `acceptance`, `lead` (default `claude`) |
| `task.assign` | `task`, `actor`, `role`, `mode`, `approved=true` | `scope`, `owner_pid`, `owner_boot_id`, `owner_host`, `lease_seconds` |
| `task.finish` | `assignment`, `result`, `summary`, `approved=true` | `ref` |
| `task.reap` | `approved=true` | `task`, `dry_run` (default `false`) |
| `task.event` | `task`, `actor`, `type`, `summary`, `approved=true` | `ref` |
| `task.status` | `task`, `to`, `actor`, `approved=true` | `summary` (default empty) |

`acceptance`, `scope`, and `ref` are arrays of strings, mirroring repeated CLI
options. Role, mode, result, and status enums match the CLI. An `rw` assignment
requires a nonempty `scope`; `ro` may omit it. Unknown arguments are rejected.
All six write tools require the literal boolean `approved: true`, checked before
opening the database. Approval is an execution gate, not identity authentication;
the lead remains responsible for recording worker activity. `task.reap` requires
approval even when `dry_run` is true. Owner arguments use integers/strings as in
the CLI, and `dry_run` must be a boolean.

Example `tools/call` request (one JSON object per line on stdio):

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"task.open","arguments":{"project":"/home/hipson47/code/Hipson","title":"Add MCP tools","objective":"Let the lead drive the ledger","acceptance":["Lifecycle works over stdio"],"approved":true}}}
```

Successful results put the same payload as CLI `--json` into
`result.content[0].text`, with `isError: false`: open/show/status return task
views, assign/finish return assignments, event returns one event, and
events/list/board return arrays; reap returns assignment/reason entries. Task and event objects conform to
`schemas/task.schema.json` and `schemas/task-event.schema.json`; assignments
use the former's `$defs.assignment` definition.

Ledger validation, missing IDs, invalid transitions/scopes, claim conflicts,
path-resolution errors, and storage failures return `isError: true` with a
redacted message in text content. The server continues accepting requests.
A claim conflict still commits its
`claim_conflict` event; read `task.events` to inspect it. Inspect committed state
before retrying a write whose response was lost. `tools/list` and
`hipson mcp serve --catalog` expose all ten input schemas and write gates.
