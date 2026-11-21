# Plan 1.8: Hipson as an agent-facing tool

Status: proposal, not started. Base: `main` at `bdfebd3` (1.7.0 released).

## Thesis

Hipson's user is the lead agent (see [ROADMAP](ROADMAP.md)). An agent does not see a screen. It sees
tool definitions, tool results and error messages, and pays for every byte of them in context and every
second in wall-clock time. So in 1.8 Hipson's "interface" is its MCP and CLI contract, and it gets the
care a product gives its UI:

1. Reads fit in a context window and come back in seconds.
2. Every read ends with the next concrete action, as a call the agent can make verbatim.
3. Every refusal says how to fix it.
4. Procedures the lead keeps getting wrong move into code.
5. Every change is measured against the baseline below, not argued.

No human UI is planned. A human view, if one is ever built, would render the same compact output.

## Baseline (measured 2026-11-21 on the owner's ledger: 22 active tasks, WSL2, hipson 1.7.0)

| Read | JSON (what MCP returns) | Text (CLI) | Wall-clock |
| --- | --- | --- | --- |
| `task board` | 4.30 MB | 2.5 kB | 30.9 s |
| `task now` | 4.45 MB | 4.0 kB | 32.2 s |
| `lead resume` | 9.17 MB | n/a | 38.6 s |
| `task show --task T-0158` | 0.75 MB | n/a | n/a |

- **Payload.** `verification_state` returns `latest` and `current` with the full per-file inventory of the
  project tree (`src/hipson/task_verification.py:342-380`). That inventory is 78% of the board's JSON. One
  INeedMoney task alone holds 288 kB of `files` in each copy.
- **Latency.** `_view` fingerprints each live task's project through `project_state` on every read, once per
  project per read (`src/hipson/ledger.py:1701-1714`). Several of those projects are large trees on `/mnt/c`.
  `board` already skips finished tasks (`ledger.py:1658-1670`), so the cost is in the live ones.
  `lead resume` fingerprints twice: the full `_view` path in `lead_snapshot`, plus its own stat-only
  `_project_state` (`lead_context.py:55, 154`).
- **Tool surface.** The MCP server exposes 34 tools (`MCP_TOOL_NAMES`, `mcp_server.py:30`). Their `tools/list`
  descriptors total about 16 kB. The CLI has 28 top-level commands, several of them deprecated surfaces.
- **Refusals.** There are 103 `LedgerError`/`CloseRefusedError` raises: 75 in `ledger.py`, 10 in `task_flow.py`,
  8 in `task_verification.py`, and the rest elsewhere. Only a handful name a command that would fix the
  problem. `LedgerError` is a bare `ValueError` subclass with no code (`ledger.py:377`). The MCP error path returns a
  plain message string (`_tool_error`, `mcp_server.py:1007`).
- **Already absent.** There is no `task show --section`, no paging on task reads, no compact mode and no error codes.
- **Dogfooding on 2026-11-20** found three defects (T-0155, T-0159, T-0160) and one lead mistake that Hipson
  could have prevented. A worktree was removed before the task bound to it was closed, so `task close`
  could not run its checks until the directory was recreated.
- **Stale tasks.** About 15 tasks have sat in `in_progress` for one to three weeks with no activity. The board
  shows them as if they were live work.

## Ground rule: projections never feed gates

Every gate keeps reading the full, freshly fingerprinted view, exactly as in 1.7. This covers:

- `set_status` and `close_task`, which call `_view` (`ledger.py:1511, 1543, 1590`);
- `task verify`;
- `task flow` (`flow_driver.py:249, 267, 317, 553, 807, 820`; `task_flow.py:61-96`);
- the internal `store.show` callers (`actor_routing`, `task_checkpoint`, `task_context`, `review_snapshot`).

The compact reads in this plan are a separate projection used only by the MCP and CLI read paths. A test
asserts that no gate caller can reach the projection. Gate semantics do not change in 1.8.

## Items

Each item ships with tests and, where it changes lead behaviour, an agent e2e scenario or a metric.

### P0-1 Bounded, fast reads

- Add `store.projection()`, a compact, read-only view for `task.board`, `task.now`, `lead.resume` and `task.show`.
  Keep `_view` and `store.show` unchanged.
- **Field contract.** The projection keeps identifiers, status, title, per-check freshness and reasons, counts and
  record ids. It drops file inventories, output tails and embedded full records. It bounds every free-text field
  (titles, objectives, summaries, reasons) with an explicit truncation marker. The contract is written down in
  `docs/TASK_LEDGER.md` and carries a `view_schema` version.
- **Details by id.** `task show --task T --section verification|assignments|findings|events`, with `--limit` and
  `--cursor`. The MCP tools take the same arguments. Section reads return full records and may fingerprint.
- **Size budget, enforced in code.** When a view reaches it, the view says so (`truncated: true`, plus the call that
  returns the rest) instead of silently dropping items.
- **No fingerprinting in list views.** Board, now and resume report the freshness recorded at the last verification
  and the time it was recorded (`freshness_recorded_at`). They label it as recorded, not re-checked. A live re-check
  happens only in `verify`, `close`, `flow` and an explicit `--refresh`. There is no HEAD-plus-mtime cache: stat
  data can miss content edits, which is why `project_state` hashes content.
- **Resume.** It stops calling the fingerprinting `_view` path and keeps its own cheap stat-only check, labelled as such.
- **Compatibility.**
  - The MCP tools gain `detail: "compact" | "full"`. In 1.8, `full` stays the default and `compact` is opt-in.
    The default flips in 1.9, announced in the changelog and in the MCP server instructions (`mcp_server.py:68-74`).
  - `lead.resume` and `tools/list` report the Hipson version and `view_schema`, so skills can tell which shape
    they got.
  - Update the internal readers of `verification.latest`/`current`: the resume text in `lead_context.py:187, 294-297`
    and `task_context.py:110, 151`. Also update the lead skills that read these tools.

Acceptance, on a fixture ledger shaped like the baseline (22 live tasks, one project with a 300 kB inventory):
- In `compact` JSON:
  - board ≤ 32 kB;
  - now ≤ 48 kB;
  - resume ≤ 64 kB.
- Board and now compute zero project fingerprints, and resume computes no full fingerprint (asserted by counting
  `project_state` calls). Wall-clock is recorded on the owner's ledger before and after, but it is not a gate.
- A test shows that no gate caller can obtain a projection. The existing gate tests pass unchanged.

### P0-2 Next actions

- In compact mode, `now` and `resume` end with `next_actions`: an ordered list of `{why, tool, arguments, cli}`.
  The order is:
  1. unacknowledged results;
  2. failing or stale checks;
  3. tasks in `review` awaiting acceptance;
  4. open blocking or major findings;
  5. stale tasks (P1-5);
  6. cleanup.
- The ordering is deterministic and derived from ledger state only. No model and no guessing.

Acceptance:
- A test validates every emitted action against the target tool's input schema.
- On the fixture, each action executed verbatim either succeeds or is refused with a P1-3 fix.
- The e2e scenario "lead resumes mid-task" is first recorded on 1.7.0 as a committed baseline artifact
  (tool calls, bytes read). On 1.8 it completes with fewer tool calls and fewer bytes read.

### P1-3 Refusals that name the fix

- `LedgerError` gains an optional `code` and `fix`, defaulting to none, so existing `except ValueError` callers keep
  working. `fix` is a list of `{tool, arguments, cli}`.
- The MCP error result carries the same data as a JSON text body with `error_schema: 1`, beside the human message.
  The CLI text output adds a `fix:` line.
- An inventory test lists every raise site in `ledger.py`, `task_flow.py`, `task_verification.py`, `task_runner.py`
  and `cli.py` and fails on any without a `code`.

Acceptance:
- Every one of those raise sites has a stable `code`.
- The 15 refusals below have a working `fix`, each covered by a test that runs the fix and gets past the refusal.
  This set covers the refusals hit in this session's work and in the agent e2e scenarios:
  - **Close and review gates:** close or review with stale checks, missing checks, failing checks, missing
    snapshot review, unacknowledged results, open blocking findings.
  - **Task state:** task not found, a transition that is not allowed.
  - **Running work:** claim conflict, rw run without scope, unavailable project directory.
  - **Verification:** verify on a moved HEAD.
  - **Flow:** flow spec invalid, flow resume mismatch, ack of an unknown assignment.
  - The final list goes into the test file.

### P1-4 Dogfooding defects

- **T-0155:** an unavailable agy model is reported as `model_unavailable` with the model name, not as a generic
  `provider_error`.
- **T-0159:** the reviewer brief includes the content of new (untracked) files within a size budget, and lists
  any it left out.
- **T-0160:** the agy `--settings` grant merges with the user's own `permissions.allow` instead of replacing it.
- **Findings after repair:** findings resolved by a `task flow` repair round are marked resolved after the
  re-review confirms them. T-0158 still shows 7 open.

Acceptance: one regression test per defect that fails on 1.7.0 and passes on 1.8.

### P1-5 Lifecycle guards and stale work (read-only)

- **Stale-task flag.** `now` flags tasks that are `in_progress` or `open` and have had no assignment, event or
  verification for N days (default 7). Each flag comes with `next_actions`: cancel, close, or reopen with a reason.
- **`hipson task cleanup --project PATH`.** It reports which tasks, assignments and claims are bound to the path.
  While any of those tasks is neither done nor cancelled, it exits non-zero and prints the calls that would close
  or cancel them. It never deletes or changes anything; removing the directory stays the caller's job. Because it
  only reads and reports, gate semantics are untouched.

Acceptance:
- The 2026-11-20 incident replays as a test: cleanup refuses and names `task close --task T-0151`.
- The stale flag fires on the fixture and not on recently active tasks.

### P2-6 A smaller default tool surface

- **A `lead` MCP profile.** It covers the task lifecycle and the reads, about 12 of the 34 tools, with shorter
  descriptions and schemas. It leaves out deprecated surfaces (see [DEPRECATIONS](DEPRECATIONS.md)).
- **Rollout.** In 1.8 the profile is opt-in (`--profile lead`) and `full` stays the default. Switching the default is
  a breaking change, so it waits for a later minor release and is announced in the changelog.

Acceptance:
- `tools/list` for `lead` is at most 6 kB, against the 16 kB baseline for `full`.
- The agent e2e suite passes on `lead`.
- `full` is unchanged.

### P2-7 Usage per task

This brings the ROADMAP's "whole-task budgets" forward from Later; the ROADMAP gets updated with it.

- **Per assignment.** Record tokens and elapsed time when the runtime reports them: the Codex rollout, agy JSON
  usage, Claude `stream-json` usage. Otherwise record `null` with the reason; never estimate.
- **Per task.** `task show` gives totals: attempts, repair rounds, tokens and time.
- **Flow comparison.** The live flow comparison reports tokens where the runtimes provide them.

Acceptance:
- A native probe on each runtime records real usage or an explicit `null` with its reason.
- No paid API calls are made.

## Order and release

`ledger.py` and the MCP error path are shared by P0-1, P1-3 and P1-5, so those run one after another:

1. P0-1, which carries the projection, the field contract and compatibility.
2. P0-2, which builds on the projection.
3. P1-3: error codes in `ledger.py` and the MCP error path.
4. P1-5: the guard and the stale flag, built on P1-3's codes.

P1-4 touches only `agent_runtimes.py`, `flow_driver.py` and the brief builder, so it can run in parallel with steps
1–4, in its own worktree. 1.8.0 ships when P0 and P1 are done and the baseline table is re-measured. P2 goes into
1.8.x or 1.9, depending on size.

Each slice runs as a Hipson task, with declared checks and an independent review on the snapshot; `task flow` is
used where a worker builds the slice. Slices that change what the MCP server exposes (P0-1, P1-3, P2-6) also get
a separate security review.

## Not in 1.8

- A human dashboard or web UI.
- New providers or integrations, paid APIs, cloud services.
- Changes to review/done gate semantics or to the trust model of worker evidence.
- Learned summaries: the compact views are deterministic projections of ledger state.
- Idempotency keys and revision preconditions on the lead's writes, so that a call retried after a timeout is
  safe. This is noted as the next agent-facing gap and goes into the ROADMAP.
