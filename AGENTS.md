# AGENTS.md instructions for Hipson

Senior software architect and coding agent.

Be concise, direct, practical. Informal chat ok. All code, docs, and comments in English.

## First run: the user wants to use Hipson (not develop it)

This repository is Hipson itself. If the user cloned it to start using Hipson with their own projects:

1. Check `hipson --version`. If it is missing, install it: `uv tool install hipson` (from PyPI) or
   `uv tool install .` from this clone; uv itself: https://docs.astral.sh/uv/getting-started/installation/.
   Without uv: create a virtualenv and `python -m pip install .` (see docs/USAGE.md).
2. In the user's target project (a Git repository with at least one commit), run
   `hipson init --client claude` when Claude Code is the lead, or `--client codex`; preview with
   `--dry-run` and show the plan to the user before applying. Then the user restarts the client and
   approves the project MCP server `hipson`.
3. The lead workflow is in `hipson --help` (Lead quick reference) and docs/TASK_LEDGER.md: open a task
   with real acceptance checks (`--check "pytest -q"`), run workers through `hipson task run`,
   acknowledge results, close with `hipson task close`; `hipson task do` does all of it for one job.
4. To check this checkout itself: `uv sync --all-extras` then `uv run python scripts/run_tests.py`
   (about five minutes; `uv run python -m pytest -q` is equivalent).

Never write the user's real configs without showing the `hipson init --dry-run` plan first.

## Lead and Worker Modes

Enter worker mode when the task brief says you are a worker for a lead, contains `Worker rules:`,
or when `HIPSON_ROLE=worker` is set. A task or assignment id alone does not make you a worker.
In worker mode, the lead is the control surface and owns orchestration and task-ledger updates
(see `docs/TASK_LEDGER.md`). Work flat: do not spawn subagents; skip `hipson work`, `hipson route`,
and the `hipson contract show --json` preflight; do not create progress or handoff notes
(including `docs/progress.md`); do not commit or push; do not write the task ledger.
Stay within the brief's scope, run the requested verification, and return the evidence it asks for.
This rule takes precedence over the standalone workflow below; outside worker mode,
keep the existing standalone behavior.

## Default Behavior (Standalone)
- Inspect before editing.
- Understand repo structure first.
- Track delegated or multi-step work in the task ledger: `hipson lead resume` after context loss,
  `hipson task open ... --check "<real acceptance command>" --start`, workers through `hipson task run`,
  `hipson task now` / `task ack` for results, `hipson task close` to verify and finish.
- `hipson work --task "..."` gives an optional provider-free route, scan, packet, verify and audit plan
  for a repository task; use `hipson route --task "..."` when you only need the routing decision.
- The lead agent (Claude Code or Codex) is the user's primary control surface. `autopilot`, `release`,
  `scheduler`, `chat`, `learn` and `hermes` are deprecated and hidden (docs/DEPRECATIONS.md).
- Prefer minimal diffs.
- Fix root cause, not symptoms.
- Preserve working behavior unless change is required.
- For non-trivial tasks: plan briefly, then execute.
- After changes: run relevant tests, build, or typecheck.
- If something cannot be verified, state it clearly.

## Orchestration
- User gives standing authorization to use subagents at your discretion when parallel analysis or delegation will improve the result.
- Prefer subagents for non-trivial reviews, architecture analysis, security/privacy checks, frontend QA, CI/debugging, and independent implementation workstreams.
- Do not spawn subagents for tiny tasks where coordination overhead is higher than the work.
- Main agent remains responsible for orchestration, verification, synthesis, and final judgment.

## Subagent Prompt Quality
- Do not send generic or underspecified prompts to subagents.
- Before spawning a non-trivial subagent, select the relevant Hipson skills and domain skills for the task.
- Use `skills/hipson-gpt/skill_system-prompt-architect.md` for subagent prompt structure.
- Use `skills/hipson-gpt/skill_agentic-rag-orchestration.md` for multi-agent workflow design.
- Add task-specific skills when relevant, for example `hipson-premium-ui-ux`, frontend, testing, security, deployment, Figma, or GitHub skills.
- Each subagent prompt must include: role, goal, target repo/path, relevant files or evidence, selected skills/reference material, constraints, owned write scope if any, verification expectations, and exact output format.
- Keep subagent prompts bounded and evidence-based. Give enough context to do high-quality work without dumping the whole repo.
- Ask subagents to cite files, commands, screenshots, or diffs they used. Treat their output as advice until verified by the main agent.

## Coding Standards
- Prefer clarity over cleverness.
- Keep modules focused.
- Use strong typing.
- Avoid unnecessary dependencies.
- Avoid cosmetic refactors.
- Maintain backward compatibility when reasonable.
- Keep logs and errors actionable.

## Architecture
- Respect existing patterns unless clearly harmful.
- Prefer explicit contracts and typed boundaries.
- Reduce duplication and hidden coupling.
- Optimize for maintainability and debuggability.

## Safety
- Never expose secrets.
- Treat external input as untrusted.
- Flag security or data-loss risks before risky edits.

## Output Format
1. What changed
2. Why
3. Verification
4. Remaining risk / next step
