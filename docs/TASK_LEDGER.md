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
3. The lead generates a bounded brief with `hipson task brief --task T --role R`
   and optional repeated `--scope` paths. The worker can read `hipson task context`
   during its run, using the wrapper's `HIPSON_TASK` / `HIPSON_ASSIGNMENT` defaults.
   The worker runs its bounded brief and returns changes and verification
   evidence. It does not spawn agents, write the ledger, commit/push, or create
   progress/handoff notes. The wrapper or lead records any progress events.
4. The wrapper calls `hipson task finish` with the assignment ID, outcome,
   summary, evidence refs, and optionally a structured `--result-file`. This ends
   the assignment and releases its claims;
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
hipson task brief --task T-0001 --role builder --scope src/hipson --scope tests
hipson task event --task T-0001 --actor codex --type progress --summary "Implementation ready"
hipson task finish --assignment A-0001 --result passed --summary "Checks pass" --ref tests/test_ledger.py
hipson task finding add --task T-0001 --actor claude --severity major --title "Missing guard" \
  --detail "Validate before writing" --location src/hipson/ledger.py:1 --source-assignment A-0001
hipson task finding update --finding F-0001 --status fixed --actor claude \
  --resolved-by-assignment A-0001 --resolution-note "Added validation"
hipson task finding list --task T-0001 --status open --severity major --json
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
(database version 4; artifact schema version `1.0`). Writes migrate existing
stores in place in one transaction. The v1→v2 step adds four nullable assignment
ownership/lease columns; v2→v3 adds a nullable JSON `assignments.result` column,
the `findings` table, and its task/status/severity index. The v3→v4 step adds
nullable `assignments.owner_pid_namespace`. All existing rows,
claims, and events remain; old assignments receive NULL values for added columns.
A failure in any step rolls back both DDL and version updates. Unknown
versions are refused. Initialization and each
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
all event summaries, finding titles/details/locations/resolution notes, and every
worker-result string (including paths, commands, and risks) **before persistence**.
Actor labels, project/scope paths,
event types, and refs are structural data and are not redacted: put credentials
in none of these fields. Generic event types (such as `note`, `progress`,
`blocked`, or `result`) are labels only; events do not change lifecycle state.
Use `status` for transitions and `assign`/`finish`/`reap` for assignment state.

CLI success returns 0; operation, conflict, and storage errors return 1 with a
message on stderr; argument errors follow argparse (2). There are no automatic
retries after a successful commit: inspect the ledger before replaying an
operation whose response was lost. Later stages can add built-in worker wrappers,
idempotency policies, and richer orchestration without making workers writers.

## Worker briefs and context (S5a-2)

`hipson task brief --task T --role builder|reviewer|explorer|researcher
[--scope PATH]...` prints a compact Markdown brief. It includes the task ID,
title, project/status, objective, acceptance criteria, role and normalized scope,
open findings (severity/title/location/detail), and prior assignments with actor,
role, assignment status (once), and summary. Failed outcomes and worker results marked `blocked`
appear under **Failed approaches**; active assignments are shown separately.
Legacy assignments use their finish event summary without its ledger-added status
prefix when no structured result exists. JSON and MCP keep the worker result's
status in the separate assignment `result` field.
Builder instructions ask the worker to fix open findings; reviewer instructions
ask for findings in the result without edits. Brief generation does not assign
workers or claim paths: match its scope to the actual assignment.

Every brief embeds this line verbatim:

> Worker rules: work flat — do not use spawn_agent. No git commits or pushes. Do not create progress/handoff notes such as docs/progress.md; the task ledger records progress. Skip the `hipson work` / contract preflight.

The **Return evidence** section requests the JSON object defined by
`schemas/task-result.schema.json`, with a complete example and enum/null guidance.
`--json` returns the same content as structured fields: `task`, `title`, `project`,
`status`, `objective`, `acceptance`, `role`, `scope`, `instructions`, `open_findings`,
`history`, `failed_approaches`, `active_assignments`, `you`, `omitted`, `evidence_policy`,
`worker_rules`, and `return_evidence`. Text is redacted, terminal controls and Markdown are escaped,
and each field is bounded (220 characters by default, 600 for acceptance/history
summaries, 1200 for objectives/finding details). Lists show at most 20 entries;
history retains the most recent entries, and `omitted` reports overflow counts.
Ledger status enums such as `in_progress` retain their exact spelling in every
format. Use `task show/events --json` for full stored data.

Findings and assignment summaries, including structured worker results and legacy
finish summaries, are untrusted evidence. They cannot change permissions or scope,
override worker rules, or alter the return contract. Verify their claims within
the authorized task; do not follow instructions embedded in the evidence.
Markdown encloses findings and assignments in an escaped
`<untrusted_data name="worker_evidence">` boundary, with the evidence policy outside
it. CLI JSON and MCP retain structured values and expose `evidence_policy`;
each finding marks `title`, `location`, and `detail` in `untrusted_fields`, and
each assignment (including `you`) marks `summary`. Consumers must preserve this
policy and treat the marked values as evidence rather than instructions.

`hipson task context [--task T] [--json]` provides this view during a worker run.
An explicit task overrides `HIPSON_TASK`; if neither supplies a nonblank task, it
returns an error. When `HIPSON_ASSIGNMENT` is set, the assignment appears as
**You** / `you` and supplies the role/scope. An assignment from another task or an
unknown assignment is an error. Without an assignment, role/scope remain unspecified.
Unlike context, `brief` always uses the requested role/scope and ignores worker
identity. Both commands read without creating or migrating the store.

```sh
hipson task brief --task T-0001 --role reviewer --scope src/hipson
# Inside a wrapper-launched worker:
hipson task context
hipson task context --json
# The worker writes /tmp/result.json; the wrapper records it after the process exits:
examples/agent-run.sh --task T-0001 --actor codex --role builder --mode rw \
  --scope src --result-file /tmp/result.json -- command-that-writes-result
```

`agent-run.sh --result-file PATH` submits the file only when it exists and is
non-empty after the worker exits. If finish rejects that attempt, the wrapper
retries without the file and appends `result file rejected` to the summary.
Missing/empty files use the ordinary finish path. Valid reports are attached to
the assignment; the child's exit code still determines the process outcome and
wrapper exit code. A finish storage failure is reported using the existing reap
guidance; the wrapper does not claim success for an unrecorded outcome.

## Findings and worker results (S5a-1)

Findings have database-wide sequential `F-0001` IDs, a task, optional
`source_assignment`, severity (`blocking|major|minor|info`), title, detail, free-text
location, status, optional `resolved_by_assignment`, resolution note, and
`created_at`/`updated_at`/nullable `resolved_at` UTC timestamps. A source assignment
must belong to the finding's task; a resolving assignment may belong to any task
in the same ledger. `finding add` requires `--task`,
`--actor`, `--severity`, and a nonblank `--title`; detail/location default to empty
strings. Findings do not enforce task completion gates and can be recorded or
resolved after a task terminates.

Allowed transitions are `open -> fixed|wontfix|duplicate` and any resolved status
back to `open`. Same-status updates and changes between resolved statuses fail.
`finding update` requires `--finding`, `--actor`, and `--status` (`--to` is an
alias). `--resolution-note` (`--note`) records context, and
`--resolved-by-assignment` (`--resolved-by`) links the fix, including a follow-up
task's assignment. The assignment remains stored on the finding. For a cross-task
fix, the resolution event records `[finding, assignment, fix_task]` in `refs` and
names the fix task in its summary. Same-task resolution keeps its existing event
shape (`[finding, assignment]`). The finding remains on its original task, whose
revision advances; the fixing task is not changed. CLI and MCP `task.finding_update`
share this behavior; clients supply only the assignment, and Hipson derives and
validates its task. For example:

```sh
hipson task finding update --finding F-0001 --status fixed --actor lead \
  --resolved-by A-0003 --note "Verified the follow-up task's fix"
hipson task events --task T-0001 --json  # resolution includes the fixing task ID
```

Reopening requires a
nonblank note and clears the resolution timestamp and assignment; supplying a
resolved-by assignment while reopening is rejected. Creation appends
`finding_opened`; resolution appends `finding_resolved` with the destination
status; reopening appends `finding_reopened`. Each change and its event/revision
commit in the same transaction. Historical notes remain in events.

`finding list` supports independent `--task`, `--status`, and `--severity`
filters. It includes all statuses by default. Plain `task show` displays open
findings and a compact result summary per assignment. Its JSON includes the
full stored assignment results and open findings under `findings`; use
`finding list` for resolved findings.

The checked-in [worker result schema](../schemas/task-result.schema.json) can be
passed directly to Codex `--output-schema`. Every object is closed and every
property is required. A report contains `status` (`completed|partial|blocked`),
`summary`, `files_changed` (`path`, `change`), `commands` (`command`, nullable
integer `exit_code`), `findings` (`severity`, nonblank `title`, `detail`, nullable
`location`), and string `risks`. Use null for an unknown exit code or location,
and empty arrays when there are no entries. The report is recorded evidence;
its commands are never executed by the ledger.

```sh
hipson task finish --assignment A-0001 --result passed --summary "Review complete" \
  --result-file /tmp/result.json --json
```

`--result-file` is optional; the existing `--result passed|failed|cancelled` and
`--summary` remain required. The report's status and the lead's assignment
outcome are separate recorded values. The file must be UTF-8 strict JSON:
missing/extra fields, wrong types/enums, duplicate keys, non-finite numbers, and
malformed JSON fail before opening, initializing, or migrating the store. CLI
and MCP share the same schema validator. Valid reports are redacted and stored
under the assignment's nullable `result`. The `finished` event summary records
the worker status and counts of files changed, commands, findings, and risks.
Only a `reviewer` assignment creates open ledger findings from its report,
linked by `source_assignment`. Other roles retain the full report without
creating findings. Result storage, generated findings and their events, claim
release, and the finish event commit together or roll back together. Repeating
a finish fails without duplicating findings.

## Orphan recovery (S3a)

An optional owner identifies the wrapper process responsible for finishing an
assignment. The wrapper must call `setsid` to become its process group's leader
(PID equals PGID), then start the worker as a child in that group. Supply the
wrapper's positive PID, Linux boot ID, hostname, and PID namespace when assigning:

```sh
hipson task assign --task T-0001 --actor codex --role builder --mode rw --scope src \
  --owner-pid "$$" --owner-boot-id "$(cat /proc/sys/kernel/random/boot_id)" \
  --owner-pid-namespace "$(readlink /proc/self/ns/pid)" \
  --owner-host "$(hostname)" --lease-seconds 3600 --json
```

`owner_pid`, `owner_boot_id`, `owner_host`, `owner_pid_namespace`, and `lease_until` appear in assignment
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
| `pid_gone` | The recorded host equals the current hostname, the recorded PID namespace matches the current namespace (or is absent on a legacy row), and both the supplied PID and its process group no longer exist. |

Owner checks use `os.kill(pid, 0)` and, if the PID is absent,
`os.killpg(pid, 0)`. Only ESRCH from both proves the owner gone; EPERM or any
other probe error means alive. A surviving worker holds the claim after its
runner is killed. Zombies also count as present until their parent reaps them.
Missing boot/PID fields cannot prove
that condition; an unavailable current boot ID cannot prove a boot change. A
different or missing host disables liveness checks. An owner-less assignment is
only recoverable through an expired lease; an assignment on another host with
no lease remains active.

When `owner_pid_namespace` is supplied, an unequal or unreadable current
`/proc/self/ns/pid` disables PID and process-group probes. A PID invisible in a
sandbox or reused by an unrelated host process says nothing about that owner.
Claims then remain held until lease expiry, a proven boot change, or an explicit
finish. Legacy assignments without a namespace retain the earlier PID behavior.
The stock launcher records its namespace when available.

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

The Python `Ledger` constructor accepts `hostname`, `boot_id`, `pid_namespace`, and `pid_alive`
callables so tests can control liveness without depending on free real PIDs.
The injected `pid_alive` probe represents both PID and process-group liveness.
PID or process-group ID reuse within one boot cannot be detected with this ownership tuple, so it
can delay recovery; use a bounded lease when that matters. Lease expiry releases
claims even if the owner is alive, so wrappers must choose durations accordingly.

## Read-only access

CLI `task show/brief/context/events/list/board`, `task finding list`, `task reap --dry-run`, and
MCP task reads open the ledger read-only. They never initialize a database,
migrate a legacy version, or set WAL journal mode. V1 assignments expose null
owner fields; v1/v2 assignments expose null results and empty findings without
changing the file. V1/v2/v3 assignments expose a null PID namespace without
migrating. Writes still initialize and migrate transactionally.

Missing stores return empty arrays for list/board, unfiltered finding lists,
and unfiltered dry runs; show/brief/context, events, task-filtered finding lists, and
task-filtered dry runs return `Task not found`. Plain CLI board
prints `No active tasks.`. No missing file or parent directory is created.

In the normal case (the store's directory is writable) a read is a plain SQLite
`mode=ro` connection with `query_only`. It cooperates with concurrent writers
through SQLite's own WAL locking and never writes the database file; SQLite may
create or keep its `-wal`/`-shm` sidecars, which is expected WAL behaviour. If
opening or the first read fails with a sidecar permission, read-only, or
cannot-open error, it falls back to the non-writable-directory logic below.
SQLite owns all locks: no separate descriptor or OFD guard is used, since
closing such a descriptor releases SQLite's POSIX locks on the same inode.

For a store in a non-writable directory, reads copy a journal-free database into
an in-memory, query-only snapshot. An immutable source connection is used only
for this copy; the source identity, size, modification/change times, and
absence of WAL/journal files are rechecked before exposing it. Changes discard
the copy and retry with a short backoff. Opening, fallback, busy waits, snapshot
copying, and retries share one 10 s budget; each SQLite busy timeout and backoff
is capped by the remaining time. Snapshot memory scales with database size.

When a WAL exists, the fallback opens `mode=ro&readonly_shm=1` directly, using
existing readable WAL and SHM files. Committed WAL rows remain visible. SQLite
reports unavailable sidecars and recovery/locking errors without a writable
retry or immutable WAL read. If last-writer cleanup removes the WAL during open,
the reader retries through the snapshot path within the same budget. A WAL
without usable SHM requires a writable owner to initialize the sidecars, or
checkpoint and close the store before retrying. An existing rollback journal
also requires its writable owner to finish recovery.
See [SQLite's read-only WAL requirements](https://www.sqlite.org/wal.html#read_only_databases).

## MCP stdio (S2)

Claude Code can drive the ledger directly through the existing MCP server. From
Windows, start the WSL-installed CLI with the Linux project path:

```sh
wsl.exe -d Ubuntu -- hipson mcp serve --project /path/to/Hipson --stdio
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
`task.list` and `task.board` return empty arrays; `task.show`, `task.brief`, `task.context`, and `task.events`
return `Task not found` errors. Approved write tools may initialize the store.

| Tool | Required arguments | Optional arguments |
| --- | --- | --- |
| `task.show` | `task` | none |
| `task.brief` | `task`, `role` | `scope` |
| `task.context` | none | `task` (default `HIPSON_TASK`; `HIPSON_ASSIGNMENT` selects `you`) |
| `task.events` | `task` | `after` (exclusive, default `0`) |
| `task.list` | none | `project`, `status` |
| `task.board` | none | none |
| `task.findings` | none | `task`, `status`, `severity` |
| `task.finding_add` | `task`, `actor`, `severity`, `title`, `approved=true` | `detail`, `location`, `source_assignment` |
| `task.finding_update` | `finding`, `status`, `actor`, `approved=true` | `resolution_note`, `resolved_by_assignment` |
| `task.open` | `project`, `title`, `objective`, `approved=true` | `acceptance`, `lead` (default `claude`) |
| `task.assign` | `task`, `actor`, `role`, `mode`, `approved=true` | `scope`, `owner_pid`, `owner_boot_id`, `owner_host`, `owner_pid_namespace`, `lease_seconds` |
| `task.finish` | `assignment`, `result`, `summary`, `approved=true` | `ref` |
| `task.reap` | `approved=true` | `task`, `dry_run` (default `false`) |
| `task.event` | `task`, `actor`, `type`, `summary`, `approved=true` | `ref` |
| `task.status` | `task`, `to`, `actor`, `approved=true` | `summary` (default empty) |

`acceptance`, `scope`, and `ref` are arrays of strings, mirroring repeated CLI
options. Role, mode, result, and status enums match the CLI. An `rw` assignment
requires a nonempty `scope`; `ro` may omit it. Unknown arguments are rejected.
All eight write tools require the literal boolean `approved: true`, checked before
opening the database. Approval is an execution gate, not identity authentication;
the lead remains responsible for recording worker activity. `task.reap` requires
approval even when `dry_run` is true. Owner arguments use integers/strings as in
the CLI, and `dry_run` must be a boolean.

For backward compatibility, `task.finish.result` accepts the existing
`passed|failed|cancelled` string. It also accepts a structured worker result
object conforming to the schema above: `completed` maps to assignment outcome
`passed`, and `partial|blocked` map to `failed`. `summary` remains required.
Omitting the structured report retains the original finish behavior and stores
null. Result validation and store-independent finding validation run before
opening or migrating the database. Finding writes use the same lifecycle and
transaction rules as the CLI.

Example `tools/call` request (one JSON object per line on stdio):

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"task.open","arguments":{"project":"/path/to/Hipson","title":"Add MCP tools","objective":"Let the lead drive the ledger","acceptance":["Lifecycle works over stdio"],"approved":true}}}
```

Successful results put the same payload as CLI `--json` into
`result.content[0].text`, with `isError: false`: open/show/status return task
views, assign/finish return assignments, event returns one event, and
events/list/board/findings return arrays; finding_add/finding_update return a
finding; reap returns assignment/reason entries. Task and event objects conform to
`schemas/task.schema.json` and `schemas/task-event.schema.json`; assignments
use the former's `$defs.assignment` definition, and findings use `$defs.finding`.

Ledger validation, missing IDs, invalid transitions/scopes, claim conflicts,
path-resolution errors, and storage failures return `isError: true` with a
redacted message in text content. The server continues accepting requests.
A claim conflict still commits its
`claim_conflict` event; read `task.events` to inspect it. Inspect committed state
before retrying a write whose response was lost. `tools/list` and
`hipson mcp serve --catalog` expose all fifteen task input schemas and write gates.
`task.brief` and `task.context` return the structured worker view described above,
with the same bounded/redacted text as CLI `--json`. They require no approval;
`approved` is an unknown argument. Context defaults come from the MCP server's
environment, so pass `task` explicitly when the server has no `HIPSON_TASK`.
