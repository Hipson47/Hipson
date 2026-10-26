# Agent e2e harness

This opt-in local harness gives a real Codex lead a natural-language mission in
a fresh fixture repository. Hipson ledger state, events, source files, and
recorded commands decide success. The agent's final message never does. These
are workflow experiments, separate from provider-free unit tests; real agent
runs are deliberately absent from CI.

## Run

Use Linux/WSL with this checkout's development environment installed, Git, Bash,
`setsid`, and `ps`. The harness uses the Python standard library and the local
Hipson package; it adds no runtime dependency.
The harness requires Linux `/proc` and `prctl` process supervision; its tests
explicitly skip on macOS and native Windows. This limit does not apply to the
macOS CLI or worker wrapper. Linux CI continues to require zero skipped tests.

```sh
# Offline: execute all six reference solutions, without invoking Codex.
.venv/bin/python scripts/agent_e2e.py --scenario all --driver fake

# On demand: authenticated Codex CLI on PATH, explicit model and effort.
.venv/bin/python scripts/agent_e2e.py --scenario all --runs 3 --driver codex \
  --model gpt-6.1-sol --effort high --timeout 300 --keep

# Exercise a known broken solution and inspect its deterministic failures.
.venv/bin/python scripts/agent_e2e.py --scenario recovery --driver fake \
  --solution e2e/agents/broken/recovery-tamper.sh

# Fast offline harness tests, also included in the normal suite.
.venv/bin/python -m pytest -q tests/test_agent_e2e.py

# Offline: re-evaluate saved snapshots and transcripts with the current oracles.
.venv/bin/python scripts/agent_e2e.py --replay .agent-e2e/<timestamp>
```

Codex runs consume the authenticated account's model usage and can incur costs.
`--runs` multiplies that usage for each selected scenario. Start with one run;
`--timeout` limits elapsed driver time, not tokens or spend. Fake runs have no
model usage. Do not run the Codex driver from inside another Codex session.
Astra models are rejected. The defaults are the fake driver, all scenarios, one
run, `gpt-6.1-sol`, high effort, and a 300-second driver timeout. Setup and cleanup
have their own short bounds outside that driver timeout.

Exit status is 0 only when every run passes; scenario, driver, transcript, oracle,
and harness errors produce status 1. Invalid CLI arguments produce status 2.

## Isolation and evidence

Each run creates a temporary Git-initialized fixture repository and an empty
ledger at `.hipson/ledger.sqlite` inside it. `HIPSON_LEDGER` and `HIPSON_HOME`
point into that writable workspace. `HIPSON_ROLE`, `HIPSON_TASK`, and
`HIPSON_ASSIGNMENT` are removed, so invoking the harness from a worker still
measures a lead. `PYTHONPATH` selects this checkout; the first `hipson` on PATH
is a recording proxy which calls this checkout's actual CLI. `HIPSON_BIN` also
selects it for the stock worker runner. Setup preconditions are excluded from
agent friction counts. Both drivers use a fresh temporary `HOME` with isolated
XDG paths. Its Bash login profile and `BASH_ENV` put the recording proxy first
even after login-shell PATH initialization; owner dotfiles and `~/.agents/skills`
are absent. Each run checks `bash -lc 'command -v hipson'` before setup.

The Codex driver inspects `codex exec --help`, requires `--ephemeral`,
`--ignore-user-config`, and `--ignore-rules`, and records the flags and exact
command in each run result. It invokes `codex exec --json -s workspace-write -C
<fixture>` with the mission on stdin, explicit model/effort, approvals set to
never, and `project_doc_max_bytes=0`. A separate temporary `CODEX_HOME` contains only a
link to the existing `auth.json`, when present; it contains none of the owner's
instructions or configuration. That home is always removed and never copied to
reports. The fixture environment starts with only PATH, locale, terminal, and
timezone settings, plus harness-owned paths and runner settings. Fake runs do not
inherit caller credentials. Codex alone additionally receives `OPENAI_API_KEY`
and `CODEX_API_KEY` when set. An unsupported CLI fails
the run instead of silently dropping isolation flags. Managed machine policy
still applies and may prevent a run.

These options follow [OpenAI's non-interactive mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
The committed `e2e/agents/fixtures/codex-events.jsonl` sample is **crafted**, not a
captured live rollout. It assumes the documented `thread.started`, `turn.*`, and
`item.*` event family, `command_execution` items with IDs/command/output/exit
code, and optional turn token usage. Updates with the same item ID count once;
unknown event types are ignored, while malformed JSON and failure events fail
the run. A future CLI format change may require a parser update.

Both drivers record Hipson calls with before/after snapshots. The fixture's
launcher proxy executes an unchanged copy of `examples/agent-run.sh` and records
its real exit status separately, including exit 75 when a lead shell handles a
refusal and exits zero. Source files are captured with snapshots so acceptance
can be checked at the moment a task becomes done. In particular, later repairs
cannot make a premature close pass. Universal checks:

- Reject visible raw SQLite/Python ledger access and overriding `HIPSON_LEDGER`.
- Mark ledger changes between recorded invocations or visible Hipson calls
  missing from the proxy audit as harness errors (`measurement_valid=false`).
  Skip agent oracles for these invalid measurements and exclude them from the
  pass-rate denominator. Read-only proxy bypasses also invalidate measurement.
- Watch the ledger directory with Linux inotify and compare file identity to
  catch deletion/replacement, including a delete-and-restore between commands.
- Replay all assignment/finish/reap events across tasks to reject overlapping
  live write claims, even when the final claims table is empty.
- Reject any task becoming done before that scenario's acceptance is met.

This is an observable workflow test, not a security boundary against a hostile
agent. Actor labels are unauthenticated; CLI/launcher logs live in the writable
fixture, and deliberately concealed read-only DB access or forged instrumentation
can evade these checks. Bypass detection compares only the first unconditional
invocation in a completed shell command (including Bash `-lc` wrappers) with the
proxy audit. Commands in `&&`, `||`, `if`, loops, or function bodies are not proof
of execution; missing lexical calls there never invalidate a measurement. This
conservative check can miss bypasses later in a compound command or script.
Review the transcript with the report. Functional parser
checks run fixture source in a bounded child process with an empty environment;
use trusted local scenarios.

On timeout the driver process group is terminated; cleanup also stops fixture
runner groups whose ownership and working directory identify this run. Cleanup
discovers surviving members through `/proc`, including groups with an exited
leader, and validates every live member's working directory before signaling.
Oracles
and `final.json` observe state **before** cleanup, so cleanup cannot repair a
failed mission. A conflict report may correctly show its live holder's claim;
the harness stops that helper after judging the run.

## Scenarios

| Mission | Preconditions | Required observable outcome |
| --- | --- | --- |
| `lead-flow` | Empty ledger, broken colon parser | Lead opens task; builder finishes; a separate reviewer returns a structured result with one finding; follow-up builder fixes it; both selected builders' claims cover `src/parser/pairs.py`; lead links and resolves the finding, then reviews and closes; parser handles spaces; all claims released. |
| `conflict` | Real runner/worker process holding `src/parser` | Another builder attempt gets launcher exit 75 and a ledger conflict; agent leaves a reasoned block, redirects to disjoint scope, or waits for normal holder completion; no live holder bypass. |
| `recovery` | An active assignment with a real exited owner and retained claims | Ledger recovery emits `reaped` with `pid_gone` before a replacement builder assignment; replacement fixes the colon parser and finishes; task remains in progress for later review. |
| `worker-dies` | Unreliable worker fixture exits 9 with partial changes and no result | Native runner journals exit, changes and log tail; lead reads activity before replacement, acknowledges both deliveries, verifies recovery and leaves task in review. Broken solution skips journal inspection. |
| `result-busy` | Parser task plus independent lead task, worker waits for a busy marker | Result arrives while another task occupies the lead; board exposes the unread delivery; lead inspects and acknowledges it before closing. Broken solution forces done without acknowledgement. |
| `resume` | Delivered unacknowledged result, open major parser finding, live writer on `src/held` | Mission says only “You lost your context; continue the work” plus the default `lead resume` output. Lead acknowledges delivery, runs a repair worker, verifies parsing and resolves the finding while preserving the held scope; no other task-history discovery. Three broken solutions omit acknowledgement, attempt a second writer, or ignore the finding. |

The native `task run` launcher is recorded as one CLI operation, including its heartbeat updates
and finish, and its actual exit is included in `runner.jsonl`. Concurrent CLI calls are serialized
by the proxy; the busy-lead scenario releases the worker while an independent-task call waits,
then observes that result still pending during that task's event. This makes delivery/consumption
ordering deterministic without editing the ledger or relying on worker claims.

Hipson inserts a reviewer's `finding_opened` immediately before `finished` in the
same transaction. The lead-flow oracle checks this actual ordering, rather than
requiring the inverse. Recovery accepts explicit `task reap` or automatic reap
on the subsequent overlapping write assignment; manual finish is insufficient.
The conflict oracle compares the complete initial/final parser maps while the
holder is active, including deletions and additions. Closing after waiting is
allowed once the parser passes and the lead moves through review before done.

The deterministic `bin/fake-worker` accepts `build|review|fix RESULT.json [TARGET.py]`, edits
the parser (or optional source target for disjoint work) for builders, and writes a valid task-result report. The reviewer
reports the whitespace finding without editing. The launcher ingests that report
and releases claims. `hold` is reserved for harness setup and waits until
`.release-holder` exists. Missions describe goals and available tooling;
`solution.sh` supplies a separate reference using documented CLI/runner commands.
Reference solutions rely on fresh-ledger IDs; agents can discover IDs normally.

## Read a report

Results go to git-ignored `.agent-e2e/<UTC timestamp>/report.json` and `report.md`.
The report includes per-scenario total/valid/invalid runs, passes, and pass rate
among valid runs (`null`/`n/a` if none). Invalid runs are labeled `INVALID` and
their `harness_errors` are separate from agent failures. It also includes
per-run failures,
command counts, exact failing Hipson invocations with exit codes and first error
lines, help/docs lookups, turns, wall duration, and token usage when present.
`--keep` retains a copy of the fixture under the run's `workspace/`; otherwise
the temporary workspace is removed. Retained ledger project paths refer to the
original temporary location and are for inspection, not direct replay.

Each run directory contains `mission.md`, redacted `transcript.jsonl`, `stderr.txt`,
`cli.jsonl`, `runner.jsonl`, `initial.json`, `final.json`, `seed.json`, and `result.json`.
Fake runs additionally contain `stdout.txt`. Errors early in setup/driver startup
can leave fewer artifacts; the run result records the failure. Reports/transcripts
are local artifacts and are not uploaded by the harness.

Driver output is captured in anonymous temporary files and scrubbed before being
retained. Transcript parsing, CLI/runner logs, errors, and JSON/Markdown reports
redact common credential patterns and credential values known to the invoking
environment, including bare values in diagnostic output. This is best-effort
redaction, not a guarantee for unknown credential formats; inspect artifacts
before sharing them. Existing saved runs are not rewritten automatically.

`--replay` accepts either a whole report directory or one scenario run directory.
It starts no driver, modifies no saved ledger, and signals no processes. It checks
recorded before/after ledger continuity, transcripts, universal rules, and current
scenario oracles against captured source. Older runs without `seed.json` use their
initial assignments. Reports go into a new timestamp directory. Replay cannot
re-observe runtime-only evidence such as inotify events, process liveness at the
original observation time, or the original authentication/isolation setup; retain
the original result when assessing those properties.

For Codex, **total commands** counts transcript command-execution items, not
individual shell statements. **Hipson invocations** comes from actual CLI proxy
calls, so nested launcher calls and failures hidden by `|| true` remain visible.
The CLI itself uses exit 1 for a claim refusal; the stock launcher maps that to
75, recorded in `worker_runs` and `runner.jsonl`. Help/docs counts use command
text heuristics, not proof that documentation was read. Duration includes setup,
driver, oracle, and cleanup time. Token fields are summed across completed turns;
missing token usage is null, not zero.

The fake driver executes a solution shell script and synthesizes the same parsed
command-event form: one script item plus the actual Hipson invocation items. Its
command/lookup counts describe that synthetic trace, not model behavior; compare
friction metrics between real runs with the same driver, CLI version, and model.

## Add a scenario

Create `e2e/agents/scenarios/<name>/` with:

1. `mission.md`: natural-language goal and available fixture tooling; do not embed
   the command solution or reveal the oracle.
2. `setup.py`: `setup(fixture) -> dict` prepares preconditions and returns seed
   identifiers. `fixture.cli(...)` runs recorded Hipson JSON calls;
   `fixture.open_task()` and `fixture.start_holder(task)` are common helpers.
   Register helper processes in `fixture.helpers` for cleanup. Do not seed by
   editing the DB directly.
3. `solution.sh`: documented `hipson` and launcher commands that fulfill the
   mission. Fake scripts run with Bash `-e -o pipefail`; handle expected refusals.
4. `oracle.py`: `check(ctx) -> list[str]` returns specific failures, or an empty
   list. It must judge state, not worker/agent claims. `ctx` exposes `workdir`,
   `ledger`, parsed `transcript`, `state`, `initial`, CLI `audit`, launcher
   `runners`, and setup `seed`. Snapshots contain `tasks` (full assignment and
   claim views), all `events`, all `findings`, and parser/source `files`.
   `ctx.task()`, `ctx.events(kind, ref)`, and `ctx.transitions(status, task)` help
   inspect the evidence. Done-time checks substitute that historical snapshot;
   use `ctx.state` for source assertions rather than reading current files.
5. Offline tests: run the reference through `run_scenario`; add a broken solution
   with a specific expected failure. Keep tests compatible with both pytest and
   `scripts/run_tests.py` (only the `tmp_path` fixture is supported by the latter).

Scenario discovery is automatic for directories containing `mission.md`.
Universal checks run for each scenario in addition to its own oracle.
