# Task ledger

## Lead quick reference

- After context loss: `hipson lead resume`
- What needs attention now: `hipson task now`
- Start with acceptance checks: `hipson task open --project . --title T --objective O --check 'pytest -q' --start`
- Run workers: `hipson task run --task T-0001 --actor codex --role builder --mode rw --scope src -- COMMAND [ARGS...]`
- Acknowledge deliveries: `hipson task ack --assignment A-0001`
- Verify and close (runs the checks): `hipson task close --task T-0001`
- One shot (open, run one worker, verify, close): `hipson task do --title T --check 'pytest -q' --actor codex --scope src -- COMMAND [ARGS...]`
- Ad-hoc verification: `hipson task verify --task T-0001 -- COMMAND [ARGS...]`

The lead (for example Claude Code or Codex) is the only orchestrator. The lead (or its worker wrappers acting on
its behalf) writes tasks, assignments, and events for Codex/agy workers. Workers
never write the ledger themselves. `actor` and `lead` are recorded labels, **not
authenticated identities**. This CLI does not enforce that operational boundary.

The database defaults to `<detect_hipson_home()>/ledger.sqlite`. `HIPSON_LEDGER`
overrides the default; `--db PATH` overrides both. Task subcommands support
`--db` and `--json` (also accepted before the subcommand); `run` preserves the child's stdout
instead of emitting a JSON envelope. The CLI and MCP tools
use the same central ledger. `task run` also launches a worker; no provider or Hermes is required.

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
5. The lead inspects the result/journal and diff, verifies acceptance criteria, explicitly
   acknowledges each ended assignment with `hipson task ack --assignment A [--note TEXT]`,
   runs the declared acceptance checks (or `hipson task verify --task T -- COMMAND [ARGS...]`) on the
   candidate, then moves the task to `review` and, once accepted, `done`; `hipson task close` does
   these steps and refuses without changing anything when a gate fails.
   Failed work can return from review to `in_progress` for another assignment.
6. For an interrupted wrapper, inspect `hipson task reap --dry-run`, then run
   `hipson task reap` to cancel proven orphans and release their claims. Reaping
   does not change task status; the lead decides how to resume or close it.

Use the native `hipson task run` launcher or an external integration owned by the lead.
A finished assignment or a `passed` result is a
recorded outcome. Only lead-run revision-bound verification satisfies the review/done evidence gate.

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
hipson task activity --assignment A-0001 --json
hipson task ack --assignment A-0001 --note "Read result and checked evidence"
hipson task finding add --task T-0001 --actor claude --severity major --title "Missing guard" \
  --detail "Validate before writing" --location src/hipson/ledger.py:1 --source-assignment A-0001
hipson task finding update --finding F-0001 --status fixed --actor claude \
  --resolved-by-assignment A-0001 --resolution-note "Added validation"
hipson task finding list --task T-0001 --status open --severity major --json
hipson task checks --task T-0001 --check "python -m pytest -q" --check "ruff check ."
hipson task verify --task T-0001
hipson task verify --task T-0001 --label acceptance -- git diff --check
hipson task close --task T-0001 --to review
hipson task status --task T-0001 --to review --actor claude
hipson task status --task T-0001 --to done --actor claude --summary "Review accepted"
hipson task show --task T-0001 --json
hipson task events --task T-0001 --after 2 --json
hipson task list --project . --status done --json
hipson task board
hipson task now --project .
hipson lead resume --project .
hipson task reap --dry-run --json
hipson task reap --task T-0001 --json
```

`task event` records free-form events such as `progress`, `note` or `decision`; `lead resume` lists
`decision` events with the task's decisions, so a lead can leave itself the next step before context runs
out. Types the ledger itself writes and later trusts (`opened`, `assigned`, `status`, `finished`, `reaped`,
`acknowledged`, `lead_verified`, `checks_set`, `claim_conflict`, `reopened`, `override`, `finding_*` and
`*_override`, except the runner's `project_mismatch_override`) are refused, so free text cannot forge a
lifecycle transition.

## Lead visibility and context recovery

`hipson task now [--project P] [--all] [--json]` reads one consistent ledger snapshot and sorts open
tasks by pending unacknowledged results/journals, blocked tasks or open blocking/major findings,
active assignments, review decisions, then other open work. Expanded terminal tasks follow.
An assignment shows its owner label, role/mode, held scope, last activity timestamp and age,
latest progress line, delivery summary and failure journal when present. Without heartbeat data,
the latest referenced progress/finish event or assignment start supplies last activity.
Task progress events without refs can supply activity when their actor matches the worker.
Reading activity never reaps assignments or releases claims. Terminal tasks collapse to one count,
including the count and IDs of tasks needing cleanup of active assignments or claims. `--all` expands
terminal tasks after open work. Fresh deliveries arriving after closure remain visible by default
and require explicit acknowledgement, including on done/cancelled tasks.

`hipson lead resume [--project P] [--all] [--json]` adds each nonterminal task's objective, acceptance,
recent status/override/acknowledgement decisions, failed attempts, open findings, verification
freshness and current Git branch, HEAD (full SHA and subject), and dirty file count per project.
`--db PATH` is supported by both commands. The project filter selects tasks in the central ledger;
it never selects a different database. Missing ledgers return empty views without creating files.
Reads neither migrate old stores nor update task revisions, acknowledgements or the Git index.
Both views use a sidecar-free, in-memory ledger snapshot. They refuse nonempty WALs and rollback
journals with a recovery message; retry after the writable owner finishes recovery and checkpoints/closes.
An unchanged zero-byte WAL can be ignored without opening it or its SHM. Source identity, size,
modification/change times and WAL/journal state are checked across the copy. These views never use
native SQLite WAL reads, even in writable directories. The ordinary read paths below retain native WAL support.
Git dirty state counts index changes, untracked paths and tracked stat mismatches. It does not hash
tracked content or inspect submodule contents; matching metadata is not proof of clean content.
This avoids Git clean/process filters entirely. Hooks, fsmonitor, external diffs and lazy fetching
are disabled or avoided, and Git output stays in memory.

Text is capped at **4,096 UTF-8 bytes**, including evidence delimiters, the closing footer and
the CLI's final newline. The budget is displayed, including with `--all`. Space is reserved for
atomic records for pending results, blocking/major findings and held write scopes before descriptions.
Each action category gets a turn before another category's backlog, and each record includes its task
and assignment/finding identity. Active records include last activity; descriptive assignment lines repeat
their identity. Remaining space goes to task headings and descriptions, including up to three recent
decisions, failures and commit references. Omitted action records, blocks and detail lines are counted;
truncated fields are marked. Use `task show --task T --json` / `task events --task T --json`, a narrower
project filter, or `--all --json` for complete history.
JSON retains complete structured results/journals for visible tasks and a `terminal_tasks` count/cleanup
summary; `--all --json` includes terminal task entries too. MCP tools return this JSON view and accept
`all: true` to expand terminal tasks. Rendering uses stable priority/task ordering and no model or provider. Resume ages
use the **ledger watermark** (latest task update or heartbeat) to keep repeated output identical
for an unchanged ledger and Git state; the timestamp and age basis are displayed. Use `task now`
for wall-clock activity ages. Git and ledger are separate observations, not an atomic cross-system snapshot.

## Change observation against scope (opt-in, 1.7)

Claims stop two writers from starting on the same paths; change observation checks afterwards what
actually changed. It is opt-in: `hipson task run --observe` (also `task do --observe`), or
`"observe_changes": true` in the project's `.hipson/policy.json`.

- Before launch, after the claim, the runner snapshots the project tree: path, size, `mtime_ns` and a
  SHA-256 of each file within the hashing budget, read from the exact regular file that was stat()ed
  (links are never followed). It never stores file contents; sensitive files such as `.env` or keys are
  compared by size and mtime only, without a digest; secret-shaped path names are stored only as digests.
  `.git` (directory or worktree pointer file), local environments (`.venv`, `venv`, `node_modules`), known
  tool caches (`__pycache__`, `.pytest_cache`, `.mypy_cache`, ...) and the active ledger with its SQLite
  sidecars and baselines (when the ledger lives inside the project) are excluded and listed in the result.
  Caps: `--observe-max-files` (directory entries visited, default 20000) and `--observe-max-bytes` of
  content hashing (default 256 MiB), or `observe_max_files` / `observe_max_bytes` in the policy (they apply
  with `--observe` too); the walk also stops below 64 directory levels. The baseline is kept privately
  beside the ledger (`.<ledger file>.observations/A-....json`), so it survives a dead supervisor. If it
  cannot be taken, the worker does not start and the assignment records `unobserved` with the reason.
- After the worker stops, on every path (success, failure, cancellation), the runner takes the final
  snapshot while the assignment still holds its claims, then compares. A file whose content hash is
  unchanged is not a change, even with a new mtime (edit then revert); without a hash, a size or mtime change
  counts and is marked `evidence: metadata`. For reaped assignments, including orphans recovered while a new
  assignment starts, the comparison is completed by `hipson task reap` (CLI or MCP) or the next `task run`.
- Each created, modified or deleted path is classified: `in_scope` (inside the `rw` assignment's claimed
  scope), `unattributed` (inside the scope of another `rw` assignment in the same project that was alive
  between this assignment's start and its final snapshot; never blamed on this worker), or `out_of_scope`.
  A read-only (`ro`) assignment has no write scope, so every change outside other writers' claimed paths
  counts as `out_of_scope`.
- `out_of_scope` changes raise one `major` finding per kind (created, modified, deleted) on the task,
  listing the paths. Like any major finding it blocks `done` until it is resolved: `fixed` after reverting,
  or `wontfix` with a reason when the change was expected. An `observed` event records the counts and a
  bounded list of changed paths.
- Status `incomplete` (with `incomplete_reasons`) when a cap is reached (`truncated: true`; only paths before
  the cap are compared), a directory or file cannot be read (nothing under it is compared), the hashing budget
  runs out, or a file changes while it is hashed. An incomplete observation never reads as clean.

The result is stored on the assignment as `changes` (in `task show`, `task now`, `lead resume` and the
MCP JSON). It is an observation of the filesystem, not attribution: the lead or another process may also
have written during the run, and paths are untrusted text. Secret-shaped path names are redacted.

## Worker environment allowlist (opt-in, 1.7)

By default a worker inherits the lead's environment (minus variables that select another git repository or
inject git configuration), so it sees every credential exported in the lead's shell. `hipson task run --env
minimal` (also `task do --env minimal`, or `"worker_env": "minimal"` in `.hipson/policy.json`) passes only an
allowlist instead:

- the login and runtime basics: `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `PWD`, `LANG`, `LANGUAGE`,
  `LC_*`, `TZ`, `TERM`, `COLORTERM`, `TMP`, `TEMP`, `TMPDIR`, `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`,
  `VIRTUAL_ENV` and the `XDG_*_HOME` / `XDG_RUNTIME_DIR` directories;
- network access: `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `ALL_PROXY` (and their lower-case forms) and CA
  bundles (`SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`,
  `GIT_SSL_CAINFO`); git identity (`GIT_AUTHOR_*`, `GIT_COMMITTER_*` names and emails);
- agent CLI config homes (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`), the WSL bridge (`WSL_DISTRO_NAME`,
  `WSL_INTEROP`, `WSLENV`) and the Windows essentials (`SYSTEMROOT`, `COMSPEC`, `PATHEXT`, `USERPROFILE`,
  `APPDATA`, `LOCALAPPDATA`, `PROGRAMFILES`, ...);
- every `HIPSON_*` variable: the lead's (`HIPSON_LEDGER`, `HIPSON_HOME`) and the worker's own.

File-based logins (`~/.codex/auth.json`, `~/.claude/.credentials.json`) keep working through `HOME`.
Credentials passed as variables never pass by default: name each one with `--env-allow NAME` (repeatable) or
`"worker_env_allow": ["NAME", ...]` in the policy, e.g. `--env-allow OPENAI_API_KEY` for an API-key Codex
login, `ANTHROPIC_API_KEY`, `GH_TOKEN` or `SSH_AUTH_SOCK`. Names match case-insensitively on Windows. Names
that select another repository or inject git configuration (`GIT_DIR`, `GIT_CONFIG_*`, ...) cannot be allowed.

- Command-line options win over the policy: `--env inherit` overrides a policy's `minimal` (and its allowed
  names); `--env-allow` adds to `worker_env_allow` and needs `minimal`. With `--env` the command line decides
  and an invalid policy contributes no allowed names. A rejected `--env-allow` value is never echoed.
- `worker_env` and `worker_env_allow` are read only from `.hipson/policy.json` (top-level keys, parsed by the
  standard JSON parser); `policy.yaml` never configures the worker environment. Without `--env`, any doubt
  refuses the run (exit 64) instead of silently inheriting everything: a `policy.yaml` in effect that may set it,
  a `policy.json` that sets it but is invalid (a top-level key that only looks like it, such as `Worker-Env`,
  `worker env` or `workerenv`, makes it invalid), or a `policy.json` that cannot be read or parsed and may set
  it. "May set it" is decided on the raw file in effect (`policy.json` shadows `policy.yaml`), read once and
  parsed from those same bytes: `worker` and `env` in any case with any separators or escapes (or none) between
  them, a `\u` escape, a NUL byte, or bytes that are not UTF-8 (UTF-16 and other encodings). A broken policy
  that never mentions it keeps the pre-1.7 behaviour.
- The assignment records the mode, never values: `{"mode": "inherit", "count": N}`, or for `minimal` the
  passed `names`, the requested `allowed` names, those `missing` from the lead's environment and the number
  `dropped`. `task show` prints an `Env minimal (...)` line and `task now` / `lead resume` an `env` line;
  the JSON and MCP views carry the record as `environment` (ledger schema v9).
- Lead verification (`task verify`, `task close`, the checks of `task do`) always inherits the environment:
  checks are the lead's own declared commands, run as the lead, and often need its toolchain or test
  credentials. The allowlist protects the lead's secrets from delegated workers, not from the lead.
- `inherit` stays the default in 1.7 so nothing changes for existing users. The plan is to make `minimal` the
  default in a later minor release, after it has been used with the real worker CLIs; `--env inherit` will
  keep the old behaviour.

## Actor routing recommendation (1.7)

`hipson route actor` answers "who should take this?" from your own actor registry and the ledger's record of
how each actor did. It is a recommendation only: it never launches, assigns or authorizes anything, and the
lead decides.

- Capability tags describe what work needs: `task open --needs code_write,web_research` (repeatable or
  comma-separated; also `task do --needs`, MCP `task.open` `needs`), and `task run --needs browser` for one run.
  An assignment records the run's tags, or the task's when the run names none. Built-in tags: `browser`,
  `computer_use`, `web_research`, `long_context`, `code_write`, `code_review`, `creative_writing`,
  `image_gen`; a registry can declare more in `capabilities`.
- The registry is user config, not part of Hipson: `<hipson home>/actors.json` (usually
  `~/.config/hipson/actors.json`, or `--config PATH`). Without one the command fails with a clear error;
  there is no built-in ranking. `hipson route actor --print-example` prints a neutral starting point in which
  every prior is equal. Each actor has an `id` (the `--actor` label you use with `task run`), `vendor`,
  optional `model`, `capabilities`, `priors` per tag (0..1, else `default_prior`, 0.5), `cost_tier`, `roles`,
  `modes`, `max_sensitivity` (`public` < `internal` < `confidential` < `secret`; default `internal`), and an
  optional `binary` looked up on `PATH` (`--no-probe` skips it) or `"available": false`.
- Hard rejections, each with its reason: a missing capability, a role or mode the actor does not take, work
  above its `max_sensitivity` (`--sensitivity`, default `internal`), unavailable, and for `--role reviewer
  --task T` the same vendor as any builder of that task (a reviewer must be independent; vendors compare
  case-insensitively, builders missing from the registry are listed in `notes`). Only verifications the task
  lead recorded in a lead context count toward history, as for review and done.
- Score per tag: the prior until the actor has `min_sample` (default 5) ended assignments in that role with that
  tag; from then on `(prior + w * history) / (1 + w)` with `w = history_weight * samples / min_sample`
  (`history_weight` default 2, so history outweighs the prior as soon as it counts). History is the success
  rate (passed, no out-of-scope change observed, and the first lead verification after it did not fail)
  minus half the rework rate (another builder started on the same task afterwards); the output also shows the
  first-pass verification rate, scope-violation rate, median duration and every sample size. An actor's score
  is the mean over the requested tags; ties break on lower `cost_tier`, then on the id. Equal registry, ledger
  and `PATH` give the same output.
- With `--task`, tags default to the task's and the recommendation is recorded as a `routed` task event (the
  recommended actor and the registry digest in its refs; `--no-record` skips it), so outcomes can later be
  traced to the decision. The MCP tool `route.actor` is read-only unless `record: true` with `approved: true`.
- Output: `hipson.actor_route` JSON (`--json`, [schemas/actor-route.schema.json](../schemas/actor-route.schema.json))
  with the request, the registry path, version and SHA-256, ranked candidates with per-tag components, the
  rejections and notes. Ledger schema v10 adds `tasks.needs` and `assignments.needs`.

## Acceptance checks and short flows (1.6)

A task can declare acceptance checks: exact commands that prove its acceptance criteria. Declare them
with `task open --check CMD` (repeatable; `--start` also moves the new task to `in_progress`), later
with `hipson task checks --task T --check CMD [--check CMD...]`, or clear them with `--clear`. Each value
is split like a POSIX shell would split it and stored as an argv list; no shell ever runs it. On Windows,
write paths in `--check` with forward slashes (a backslash is an escape character); MCP takes argv lists
directly. Up to 16 checks of up to 64 arguments; arguments must not contain control characters or
secret-shaped text (pass secrets through the environment). Replacing or clearing declared checks needs
`--reason`, every change is a `checks_set` event, and both CLI and MCP refuse it in a worker context, so a
worker cannot rewrite what accepts its own work. Worker briefs list the checks.

With checks declared, `review` and `done` need, for **each** check, its latest lead verification (exact
argv match) to pass under the freshness rules below. Other verifications are kept as evidence but cannot
stand in for a check. Without declared checks the previous rule applies: the latest lead verification
decides. `task show` and the JSON view list every check with `freshness` (`missing`, `current`, `stale`,
`failing`, `partial`, `unknown`) and its record; `verification.accepted_ids` names the accepted records,
which the status event references.

- `hipson task verify --task T` without a command runs every declared check in order and stops at the
  first failure (exit: that check's code). `--json` prints the list of records. MCP `task.verify` without
  `command` does the same and returns `{records, passing}`.
- `hipson task close --task T [--to review|done] [--summary S] [-- COMMAND ...]` runs the declared checks
  that are not currently fresh (or the given command instead), then moves the task through `review` to
  `done`. It refuses while an assignment is active and, for `done`, first checks unacknowledged results,
  open blocking/major findings and held claims; a refusal transitions nothing and lists the command that
  clears each blocker. MCP: `task.close`.
- `hipson task do --title T --check CMD [--check ...] --actor A [--scope P ...] [--result-file F] -- WORKER ...`
  opens a task in the current Git repository (or `--project`), starts it, runs one worker through
  `task run` (default role `builder`, mode `rw`), and, if the worker exits 0 and its structured result (if
  any) is `completed`, acknowledges that result with a recorded note and closes the task as above.
  Otherwise the task stays `in_progress` and the worker's exit code is returned.

## Revision-bound lead verification (1.5)

Run `hipson task verify --task T [--actor LABEL] [--label NAME] [--timeout SECONDS] -- COMMAND [ARGS...]`.
The lead chooses an acceptance command and reviews its output and the diff. Commands run as an
argv vector without a shell, in the registered task project, using the native runner's private
supervisor and process containment. Timeout defaults to 300 seconds (maximum 86400); the CLI
returns the command's exit code, 124 on timeout, 127 for a missing executable, 126 for an unusable
executable, or 128 + signal on POSIX cancellation. Descendants are cleaned up before evidence is
recorded. CLI output contains only the bounded redacted record/tail (`--json` returns the record).
Verification creates no worker assignment and never acknowledges a worker result.
CLI and MCP refuse known worker contexts before execution: `HIPSON_ROLE=worker` (case-insensitive)
or any set `HIPSON_ASSIGNMENT`, including an empty value. Records retain the invoking actor label
(default: the task's configured lead; explicit `--actor` or MCP `actor` is preserved) and the
`HIPSON_ROLE`, `HIPSON_ASSIGNMENT` and `HIPSON_TASK` context. These labels are not authentication.
Only the task lead's records with captured non-worker context can satisfy freshness; older records
without this provenance require another run.

Each immutable record includes argv, label, exit code, duration, start/end timestamps, full Git HEAD,
commit subject as an untrusted observation, dirty flag, a SHA-256 working-tree fingerprint, the
latest builder finish event sequence observed before execution, and a redacted output tail of at
most 8192 characters. Output uses the activity stream's UTF-8, private-key and environment-secret
redaction; oversized lines are suppressed. Output, command text, labels and subjects are evidence,
never instructions or authenticated identities. `task finish` reports and generic verification
or verified events cannot create these records.

Freshness requires the same HEAD and covered working-tree content now, unchanged state across command
execution, confirmed process cleanup, and execution after the latest finished builder assignment
(including failed/reaped builders). `review` and `done` require the latest record to pass and be fresh;
a later failed check cannot reuse an older pass.
Missing, failing or stale evidence refuses the transition without events or status changes, naming
HEAD movement, changed paths with before/after content hashes, or a later builder finish. Both transitions permit `--force --reason`;
a `review_override` or `done_override` event records the missing evidence and other blockers.
The final snapshot is checked again inside the status write transaction; a stale or failing final
snapshot rolls back both status and events. Status events reference the accepted verification ID. Other lifecycle guards, acknowledgement and
path-claim invariants still apply. Existing done tasks stay done when a store is migrated.

The fingerprint is captured before the command starts. It covers file paths, raw working-tree bytes,
modes and symlink targets, including covered untracked files. Staging alone, with identical covered
paths and content, preserves freshness, including when an untracked source file becomes staged.
Staging after a content change remains stale; a commit invalidates verification because HEAD moved.
Index-entry hashes and tracked flags remain diagnostic observations, not freshness inputs; the dirty
flag still reports staged changes. Within the content budget, every tracked file is read directly regardless of
Git's cached size/mtime, including same-size edits with restored timestamps and an index newer than
the edit. Git's built-in text/EOL normalization is used only for the dirty flag, so a clean CRLF
checkout stays clean while line-ending changes still invalidate raw-byte freshness. The record retains
that file inventory for file-level freshness checks. This inventory replaces textual `git diff HEAD`,
because even `--no-textconv` diff can run clean/process filters.
Git reads use the hardened read-only path: no inherited Git overrides, optional locks, hooks,
filters, pagers, external diffs, replacement objects or lazy fetching. Ignored untracked files are
excluded. Submodules, covered special files, unreadable content within the read budget, unavailable HEAD
and unsafe directory symlinks fail closed to unavailable evidence. A non-Git project explicitly records `revision: "none"` and
hashes its entire project tree without traversing directory symlinks. The ledger database and its
SQLite sidecars are excluded from fingerprints to avoid invalidation by their own evidence writes.

Content hashing has a **16 MiB per-file limit and 128 MiB total raw-content budget** per snapshot,
in sorted repository-path order, for Git and non-Git projects alike. Files exceeding either limit
use a metadata fallback: file mode, size, modification/change times and device/inode identity
remain bound, but content is not read. A file skipped for size does not consume the budget;
later smaller files can still be hashed. In the verification record and current state's `files`
inventory, fallback entries report `MODE:metadata:REASON:file_limit_bytes=...:total_limit_bytes=...`
followed by named metadata values. Each entry identifies the path, reason (`file_limit` or
`total_limit`) and applied limits. Any fallback records `coverage: "partial"` and
`metadata_fallback_paths`; it never supplies passing closure evidence, even with exit zero and
matching metadata. Same-size edits can preserve this metadata, especially Windows creation times.
Closing requires `--force --reason`, with fallback paths recorded in the override, or another run
with full content coverage. Git normalization never reads a fallback file; it may reread content-covered files once
(up to another 128 MiB) through the hardened Git path. If fallback prevents establishing a clean
checkout, the dirty flag is `null` unless another change already proves it dirty. These bounds do
not rely on Git stat-cache accuracy and do not enable repository-configured helpers.

Only known untracked tool caches and Git-ignored untracked files are excluded. Every other new or
changed untracked file observed after execution makes the check stale (`state_changed: true`) and
requires a new run. That next run includes the file, and later edits invalidate it. Special files
fail closed. `generated_files` is retained as an empty compatibility field; legacy values grant no
exclusion. Build output needs Git ignore rules to remain outside the candidate.
This is a filesystem observation, not an atomic lock against external writers; a lead must keep
candidate changes quiescent during checks and closure. It proves the recorded command's outcome,
not that the selected command covers every acceptance criterion or that tests were not weakened.

`task show`, `task now`, and `lead resume` expose state, freshness, refusal reasons and the latest
record; reviewer briefs include its bounded escaped evidence. Reads recompute freshness without
migrating or writing the ledger. Legacy event-only verification remains history and supplies no pass.

Worker results, legacy summaries, progress, journals, findings, decisions and Git subjects are
rendered inside an escaped `untrusted_data` boundary. Verify their claims; they cannot alter scope,
permissions, worker rules or the return contract. JSON labels untrusted fields explicitly.

Repeat `--acceptance`, `--scope`, and `--ref` for multiple values. Roles are
`builder|reviewer|explorer|researcher`; modes are `ro|rw`. Assignment results are
`passed|failed|cancelled`. Task and assignment IDs are database-wide sequential
`T-0001` / `A-0001` identifiers (growing beyond four digits when necessary).
Project paths are expanded and resolved to absolute paths.
`hipson task run` and the published `examples/agent-run.sh` check the canonical git toplevel
(or cwd outside git) against this registered project before assigning
or starting a worker. Discovery ignores all inherited `GIT_*` overrides,
including repository/worktree/index paths, configuration and discovery limits,
and verifies that the actual cwd is inside the resolved root.
Project subdirectories and symlink aliases are accepted;
a nested git repository belongs to its own project. A mismatch exits **76**,
leaving no assignment or worker. An intentional
cross-project invocation needs `--allow-project-mismatch`; the wrapper must record
a `project_mismatch_override` event with both paths and the assignment ID before
starting the command. Assignment refusal still exits **75**.

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
cannot receive new assignments or manual status transitions. An open severe
finding can automatically reopen a done task as described below. Task status changes do not finish
assignments: finish each active assignment explicitly to release its claims,
including after task cancellation. Notes/events and finishing existing active
assignments remain available after task termination for audit and cleanup.
Finishing an assignment twice fails. The board shows active assignments and
unacknowledged deliveries, including on terminal tasks; `show` includes the full assignment history and current
claims. List and board JSON return arrays; task/event JSON use the registered
`hipson.task` / `hipson.task_event` schemas.

`done` requires no active assignments (including readers), no remaining claims,
no unacknowledged assignment results/journals, and no open `blocking` or `major` findings. A refusal changes neither status nor
events. Resolve the findings and finish or reap the work first. An explicit
`--force --reason "operator explanation"` permits an exception from `review`:
a separate `done_override` event lists every active assignment, claim, unacknowledged delivery and severe
finding with the reason. It commits atomically with the status event and leaves
those assignments and claims intact for explicit cleanup. It does not bypass
the lifecycle transition rules. Every done status event lists any remaining
open `minor`/`info` findings, including their IDs. MCP `task.status` follows the
same rules through `force` and `reason`.

Creating or reopening a `blocking`/`major` finding on a `done` task atomically
returns it to `in_progress`, making it visible on the board again. A `reopened`
event names and references the triggering finding. The finding, task status,
events, and revisions commit together under the same writer lock, including
when a finding arrives concurrently with closure or through reviewer-result
ingestion after forced closure. The finding is always accepted; an earlier
force override does not suppress reopening. Other task statuses and
`minor`/`info` findings keep their current task status.

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
All user-controlled ledger text is pattern-redacted before persistence, including
actor/lead labels, refs, event types, owner metadata and worker results. Read paths
also redact legacy rows without rewriting them. Project paths and write scope
paths containing secret-shaped text are rejected: masking a coordination path
would change which tree or files its claims protect. Read-only scope labels can
be masked safely. Redaction is best effort; never supply real credentials.

SQLite uses WAL, foreign keys, a 10-second busy timeout, and a version table
(database version 6; artifact schema version `1.0`). Writes migrate existing
stores in place in one transaction. The v1→v2 step adds four nullable assignment
ownership/lease columns; v2→v3 adds a nullable JSON `assignments.result` column,
the `findings` table, and its task/status/severity index. The v3→v4 step adds
nullable `assignments.owner_pid_namespace`. The v4→v5 step adds nullable JSON `activity`
and `failure_journal`, nullable `acknowledged_at`, and `ack_note` (default empty).
The v5→v6 step adds an append-only `verifications` table and task/sequence index.
All existing rows, claims, events, and done statuses remain. Old assignments receive NULL values for added
nullable columns; ended legacy assignments retain the 1.4 `not_required` acknowledgement exemption.
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

## Assignment activity and delivery acknowledgement (1.4a)

`hipson task activity --assignment A [--json]` returns an assignment with its current
activity, end journal, result and acknowledgement state. The native runner observes a heartbeat
once per second, the last complete stdout/stderr lines, and worker-declared progress. Output
continues to the caller unchanged; persisted observations are redacted. A worker can append
one line per action to `$HIPSON_PROGRESS_FILE`, supplied by the runner, without writing the ledger:

```sh
printf '%s\n' 'Finished parser inspection; running focused tests' >> "$HIPSON_PROGRESS_FILE"
```

The runner retains at most 20 observations, 1024 characters per observation, and reads at most
64 KiB of progress per heartbeat; backlogged bytes await later heartbeats. Finalization drains unread
progress through the observed EOF in 64-KiB batches, preserving multiline-key redaction state and
retaining only the latest 20 observations. Later appends cannot prolong that scan; finalization time
depends on the unread backlog. It keeps the original file descriptor so replacing the progress path
cannot redirect its reads. Oversized lines are discarded. Observations support UTF-8; malformed UTF-8
or NUL-bearing streams (including UTF-16/32) are suppressed from the first detected encoding violation.
Raw stdout/stderr relay is unchanged. Credential patterns, known credential environment values
(deduplicated and masked longest-first), and multiline private keys are masked before persistence.
Progress file disk use is controlled by its writer; these bounds limit memory and ledger storage.
The latest heartbeat is a supervisor observation time, not proof of completed worker work.
Heartbeat/progress writes neither renew leases nor append task events/revisions. Silent or stale
activity never releases claims; existing owner-liveness and lease rules still decide recovery.

On exit, the native runner records `failure_journal` with `result_state` (`none`, `rejected`,
`valid`), shell-compatible exit code, observed signal, duration, up to 100 changed-file status lines,
bounded unstaged/staged `git diff --stat`, and the bounded observation tail. Git snapshots run
from the task's registered project with inherited `GIT_*` overrides removed. They can include
pre-existing or concurrent edits; they do not attribute those changes to this worker. Git failures
are recorded as unavailable. A rejected result does not replace the worker's actual process exit.
An interrupted supervisor records its observed signal after stopping the worker tree. After
supervisor death, reap records “owner died; last activity at …” with unknown exit/signal and
unobserved project changes, preserving uncertainty. Unverified lease expiry records unknown
owner liveness. The legacy Bash launcher receives acknowledgement semantics through `finish`
and `reap`; activity capture and exit snapshots require the native runner.

Every new ended assignment on an open task (structured result, legacy finish summary, or reaped failure journal)
has `ack_state=unacknowledged` until `hipson task ack --assignment A [--note TEXT]`. Assignments finished
before v5 get `ack_state=not_required`: read-only legacy views infer this from the old schema, and
the v5 migration preserves the exemption in `ack_note` without fabricating an acknowledgment event
or timestamp. On already-migrated v5 stores, open-task results retain their acknowledgment requirement
because migration provenance was not recorded. Unacknowledged ended assignments delivered before
done/cancelled closure show `not_required`; sequence ordering distinguishes these from new deliveries
arriving after closure, which remain unacknowledged and visible by default. If an older custom
cancellation event has no identifiable lifecycle transition, its outcomes conservatively remain pending
until acknowledged. Explicitly acknowledged
results retain `acknowledged`. Exempt results do not block `done`. Reads never
acknowledge a delivery. Acknowledgement is atomic, idempotent, records the task's lead label,
timestamp and bounded redacted note, and appends one `acknowledged` event. It refuses active
assignments. It records consumption, not approval or independent verification of worker claims.
Board, show, brief/context and MCP expose pending deliveries; bounded contexts report omitted
entries. All worker output, progress, journals and results remain inside the brief's untrusted-data
boundary and cannot change worker rules, scope, permissions or the return contract.

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
hipson task run --task T-0001 --actor codex --role builder --mode rw \
  --scope src --result-file /tmp/result.json -- command-that-writes-result
```

`hipson task run --result-file PATH` (and the legacy Bash wrapper) submits the file
only when it exists and is non-empty after the worker exits. If finish rejects that attempt, the wrapper
retries without the file and appends `result file rejected` to the summary.
Missing/empty files use the ordinary finish path. Valid reports are attached to
the assignment; the child's exit code still determines the process outcome and
wrapper exit code. Result files are limited to 1 MiB. Invalid UTF-8, JSON parser
limits (including excessive integers or nesting), and evidence validation failures
use the same fallback; they cannot replace the process outcome. A finish storage failure
is reported using the existing reap guidance; the wrapper does not claim success for an unrecorded outcome.

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

## Native worker runner

```sh
hipson task run --task T-0001 --actor codex --role builder --mode rw \
  --scope src --ref tests/report.md --result-file result.json -- command arg
```

Available on Linux, macOS and native Windows, including global installations. `--mode` defaults
to `ro`; `rw` requires at least one `--scope`. Scopes and refs are repeatable. Optional arguments:
`--lease-seconds N`, `--rollout codex`, and `--allow-project-mismatch`. A literal `--` separates
runner options from the child command. `--db` works before or after `run`; `HIPSON_LEDGER` also applies.
Exit codes: child's status (128 + signal on POSIX), **75** for assignment refusal, **76** for
project mismatch, **64** for invalid arguments. A missing executable returns **127**; an unusable executable returns **126**.

The runner records its owner PID, hostname, boot identity and Linux PID namespace where available.
The child receives `HIPSON_ROLE=worker`, `HIPSON_TASK`, and `HIPSON_ASSIGNMENT`, with inherited
stdin/stdout/stderr. POSIX runners always establish a private session/process group before
assignment. When the invoking runner already leads a group, it forks a private supervisor and
proxies signals and the exit code; the supervisor's PID is the recorded owner. Existing members
of the caller's group are excluded from cleanup. The worker shares the owner's group so
surviving descendants retain claims after runner SIGKILL. INT/TERM/HUP are forwarded, then remaining processes are
killed after a grace period. The runner stops leftover descendants even after a successful child exit,
then records finish. `AGENT_RUN_GRACE_SECONDS` controls the grace period (default 15 seconds).

On Linux the owner enables `PR_SET_CHILD_SUBREAPER` before assignment. Orphans are adopted by
the owner even after a descendant calls `setsid`; all living descendants retain claims through
grace and escalation, and adopted zombies are reaped. Kernel child-state checks prevent an
empty process snapshot from releasing claims during reparenting. On macOS process-tree snapshots track
observed descendants by PID and start time. This is best effort: a descendant that detaches and
loses its ancestry between snapshots may escape tracking. macOS has no Linux subreaper support.

Before Windows worker creation, the runner joins a non-inheritable, non-breakaway guardian Job
with `KILL_ON_JOB_CLOSE`. The child inherits membership atomically during creation, including
the interval before `Popen` returns. It starts suspended with `CREATE_NEW_PROCESS_GROUP`, joins
a nested worker Job, then resumes. The worker Job permits cleanup before ledger finish without
terminating the runner. The guardian handle stays open until the interpreter exits; Windows
closes it on runner death and kills the whole tree, including a child still in startup.
This uses [Windows Job inheritance and nesting](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
Ctrl+C/Ctrl+Break are forwarded as Ctrl+Break; after grace the worker job is terminated.
Job process counts confirm cleanup before finish. PID liveness checks use query-only handles,
never `os.kill` probes. If containment cannot be established the worker does not execute.
After a hard-killed runner, use `hipson task reap` to release the orphaned assignment.

`--rollout codex` appends a unique `ledger-run-UUID` marker to the stdin brief and matches only
session JSONL files created/modified since this run that contain that marker. It records model,
reasoning effort and rollout path, or explicit unknown facts if none is found. These are worker
process facts, not lead verification; the worker report cannot complete the task itself.

## Orphan recovery (S3a)

An optional owner identifies the runner process responsible for finishing an
assignment. On POSIX the wrapper must call `setsid` (the binary or Python's `os.setsid()`) to become its process group's leader
(PID equals PGID), then start the worker as a child in that group. Supply the
wrapper's positive PID, boot ID, hostname, and Linux PID namespace when assigning.
On Linux/WSL2:

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

Only active assignments are considered. A verified live owner keeps its assignment
and claims even after lease expiry: its hostname and boot ID must match the local
identity, its PID namespace must match (both absent on macOS/native Windows), and
its PID or POSIX process group must still be alive. Lease expiry applies only to
owner-less or unverifiable owners. Recovery uses these conditions:

| Reason | Condition |
| --- | --- |
| `boot_id_changed` | The recorded host equals the current hostname, and the supplied boot ID differs from the current Linux boot ID or macOS boot-session UUID. |
| `pid_gone` | The recorded host equals the current hostname, any recorded PID namespace matches the current namespace, and the supplied PID (plus its POSIX group) no longer exists, with no identifiable detached Linux worker remaining. |
| `lease_expired` | No verified live owner protects the assignment, and its lease has expired. |

On Linux/macOS, owner checks use `os.kill(pid, 0)` and, if the PID is absent,
`os.killpg(pid, 0)`. Only ESRCH from both proves the owner gone; EPERM or any
other probe error means alive. A surviving worker holds the claim after its
runner is killed. Zombies also count as present until their parent reaps them.
Linux `task run` also supplies inherited `HIPSON_RUN_OWNER` and `HIPSON_RUN_DB` markers.
After the owner and original group disappear, recovery checks bounded `/proc/*/environ` reads
for those markers plus `HIPSON_ASSIGNMENT`. A matching detached process retains the claim.
Host, boot and PID-namespace checks still apply before probing. This is best effort after owner
SIGKILL: descendants with removed markers, unreadable environments or markers beyond the 1 MiB
read limit cannot be identified from the existing ledger owner fields. On macOS, detached descendants cannot be
rediscovered after owner death; only the original group remains recoverable. Use explicit
finish only after checking such descendants. The legacy Bash wrapper has only group tracking.
On native Windows, a query-only process handle checks `GetExitCodeProcess == STILL_ACTIVE`;
no signal is sent and no process group is probed. Access denied and unknown query errors
keep claims active. Windows has no boot identity here, so PID reuse after reboot is guarded
only by lease expiry or explicit finish. Use leases for native Windows workers.
`task run` uses a Job Object to terminate descendants when its owner dies; manually managed
workers need their own cleanup. The Bash wrapper still requires WSL2 on Windows.
macOS boot identity is obtained through a bounded `sysctl -n kern.bootsessionuuid` call, returning unknown on failure.
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
The stock launcher records its namespace when available; namespaces are Linux-only.

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
PID or process-group ID reuse within one boot cannot be detected with this ownership
tuple, so it can delay recovery. A verified live owner is never displaced merely
because its lease expired. Inspect and explicitly finish an assignment when its
recorded identity is no longer trustworthy.

## Read-only access

CLI `task show/brief/context/activity/events/list/board`, `task finding list`, `task reap --dry-run`, and
MCP task reads open the ledger read-only. They never initialize a database,
migrate a legacy version, or set WAL journal mode. V1 assignments expose null
owner fields; v1/v2 assignments expose null results and empty findings without
changing the file. V1/v2/v3 assignments expose a null PID namespace without
migrating. V1–v4 reads expose null activity/journals/ack timestamps without migrating.
Writes still initialize and migrate transactionally.

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

Claude Code can drive the ledger directly through the existing MCP server on Linux,
macOS, native Windows or WSL2. See [INSTALL.md](INSTALL.md) for per-OS
`claude mcp add --transport stdio` commands. To start the WSL-installed server
from Windows, use its Linux project path:

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
`task.list` and `task.board` return empty arrays; `task.now` and `lead.resume` return empty view objects;
`task.show`, `task.brief`, `task.context`, and `task.events`
return `Task not found` errors. Approved write tools may initialize the store.

| Tool | Required arguments | Optional arguments |
| --- | --- | --- |
| `task.show` | `task` | none |
| `task.brief` | `task`, `role` | `scope` |
| `task.context` | none | `task` (default `HIPSON_TASK`; `HIPSON_ASSIGNMENT` selects `you`) |
| `task.events` | `task` | `after` (exclusive, default `0`) |
| `task.list` | none | `project`, `status` |
| `task.board` | none | none |
| `task.now` | none | `project`, `all` |
| `lead.resume` | none | `project`, `all` |
| `task.findings` | none | `task`, `status`, `severity` |
| `task.finding_add` | `task`, `actor`, `severity`, `title`, `approved=true` | `detail`, `location`, `source_assignment` |
| `task.finding_update` | `finding`, `status`, `actor`, `approved=true` | `resolution_note`, `resolved_by_assignment` |
| `task.open` | `project`, `title`, `objective`, `approved=true` | `acceptance`, `lead` (default `claude`) |
| `task.assign` | `task`, `actor`, `role`, `mode`, `approved=true` | `scope`, `owner_pid`, `owner_boot_id`, `owner_host`, `owner_pid_namespace`, `lease_seconds` |
| `task.finish` | `assignment`, `result`, `summary`, `approved=true` | `ref` |
| `task.activity` | `assignment` | none |
| `task.ack` | `assignment`, `approved=true` | `note` |
| `task.reap` | `approved=true` | `task`, `dry_run` (default `false`) |
| `task.event` | `task`, `actor`, `type`, `summary`, `approved=true` | `ref` |
| `task.verify` | `task`, `command` (argv array), `approved=true` | `label`, `timeout` (seconds, default 300) |
| `task.status` | `task`, `to`, `actor`, `approved=true` | `summary` (default empty), `force` (default `false`), `reason` (required with `force`) |

`acceptance`, `scope`, and `ref` are arrays of strings, mirroring repeated CLI
options. Role, mode, result, and status enums match the CLI. An `rw` assignment
requires a nonempty `scope`; `ro` may omit it. Unknown arguments are rejected.
All gated write tools require the literal boolean `approved: true`, checked before
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
finding; reap returns assignment/reason entries; verify returns its recorded evidence even when the
command fails. Task and event objects conform to
`schemas/task.schema.json` and `schemas/task-event.schema.json`; assignments
use the former's `$defs.assignment` definition, and findings use `$defs.finding`.

Ledger validation, missing IDs, invalid transitions/scopes, claim conflicts,
path-resolution errors, and storage failures return `isError: true` with a
redacted message in text content. The server continues accepting requests.
A claim conflict still commits its
`claim_conflict` event; read `task.events` to inspect it. Inspect committed state
before retrying a write whose response was lost. `tools/list` and
`hipson mcp serve --catalog` expose the task input schemas and write gates, including `task.verify`.
`task.brief` and `task.context` return the structured worker view described above,
with the same bounded/redacted text as CLI `--json`. They require no approval;
`approved` is an unknown argument. Context defaults come from the MCP server's
environment, so pass `task` explicitly when the server has no `HIPSON_TASK`.
