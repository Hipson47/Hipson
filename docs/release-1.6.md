# Hipson 1.6.0

Hipson 1.6.0 makes acceptance explicit and closing cheap. A task can declare the exact commands that prove
it is done; `review` and `done` then need a fresh pass of each of them, and one command runs them and
closes the task. It also fixes what a cold-start audit found when a new agent set Hipson up from a fresh
clone in an isolated home.

Status: **beta**.

## Highlights

- **Declared acceptance checks.** `hipson task open --check "pytest -q" [--check ...]` (and
  `hipson task checks`, MCP `checks`/`task.checks`) stores argv lists that run without a shell. With checks
  declared, `review` and `done` need the latest lead verification of **each** check to pass on the current
  HEAD and working-tree content after the latest worker result; an unrelated passing command no longer
  counts. Replacing checks needs a reason, every change is an audited `checks_set` event, and workers
  cannot change them. Worker briefs list the checks. Tasks without checks keep the 1.5 rule.
- **`hipson task close`** (MCP `task.close`): runs the declared checks that are not fresh (or one given
  command), then moves the task through `review` to `done`. A refusal changes nothing and names the command
  that clears each blocker (unread result, open major finding, active work, failing check).
- **`hipson task do`**: open a task with checks, run one worker through `task run`, acknowledge its result
  with a recorded note, run the checks and close, in one command. A failed or partial worker leaves the task
  `in_progress`.
- **`hipson task verify --task T`** without a command runs every declared check in order and stops at the
  first failure. `task open --start` moves a new task straight to `in_progress`.

## First-run fixes (cold-start audit)

- INSTALL's first task can now finish (it uses `--check` and `task close`), and the README smoke test is
  labeled as one. The real-worker example generates its brief first.
- The clone route runs Hipson from your project with `uv run --project /path/to/Hipson`, so `task open
  --project .` registers your project rather than Hipson itself.
- `hipson init` in auto mode no longer lets a Codex registration for another project block Claude Code
  setup: Codex is left unchanged with a warning. Init warns about a repository without commits and prints
  the Claude Code approval step and the 1.6 first-task commands.
- `task verify` warns when its record cannot be closure evidence (for example no initial commit).
- MCP `initialize` reports the package version and carries the lead lifecycle as instructions; tool
  descriptions explain transitions, `approved=true` and that `task.assign` launches nothing.
- Refusals name the next command: missing verification, invalid transitions (with the allowed targets),
  claim conflicts and unknown tasks. `lead resume` no longer lists a passed worker without a structured
  report as a failed attempt.
- `CLAUDE.md` (importing `AGENTS.md`) and an AGENTS.md first-run section tell a fresh agent how to set
  Hipson up for a user; product wording is lead-neutral.

## Measured

A new agent e2e scenario, `checks-close`, requires closing on a fresh pass of exactly the declared check;
its broken solution weakens the check and must fail. Live Codex measurements are listed in
[docs/AGENT_E2E.md](AGENT_E2E.md).

## Upgrade notes

- Ledger schema v7 adds `tasks.checks` (empty for existing tasks); older stores migrate on the first write.
- `hipson task open` without `--json` now prints one line (`T-0001 [open] acceptance checks: ...`); scripts
  that parse its output should use `--json`.
- MCP `task.verify` without `command` returns `{records, passing}`.
