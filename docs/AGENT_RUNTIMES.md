# Native agent runtimes: Claude Code, Codex, Antigravity

Hipson makes the agent CLIs you already have more reliable; it does not replace or emulate them.
`src/hipson/agent_runtimes.py` provides two commands:

- `hipson agent runtimes [--runtime claude|codex|agy] [--project P] [--json]`: an evidence-backed inventory.
- `hipson agent exec --runtime R [--role builder|reviewer] [--profile native|controlled] [--model M] [--effort E]
  [--continue-session] [--timeout S]`: a worker command for `hipson task run` / `hipson task flow`.

## Evidence levels

`hipson agent runtimes` keeps these apart and never infers one from another:

| Level | Meaning | Source |
| --- | --- | --- |
| `documented` | the installed CLI's `--help` lists the flags the capability needs | `<cli> --help` (and `codex exec --help`) |
| `unsupported` | the help lacks them, or the runtime documents no such mode | same |
| configured / enabled | configuration declares it (skills, plugins with their enabled flag, MCP servers, hooks, subagents, instructions) | config files and the CLIs' own list commands; no server is started |
| authorized | the CLI's status command says it is logged in | `claude auth status`, `codex login status`; agy has none |
| `verified` | a Hipson-launched run exercised it | written by `hipson agent exec` to `<hipson home>/runtimes/<runtime>.verified.json` |

Workspace-provided executables (a project `.mcp.json`, project hooks in `.claude/settings.json`, a project
`.codex/config.toml`) are listed under `workspace_executables`: review them before an unattended run.

## Capability matrix (installed versions on 2026-11-19, WSL2)

| | Claude Code 2.1.319 | Codex CLI 0.169.0 | Antigravity 1.5.5 to 1.5.11 |
| --- | --- | --- | --- |
| Non-interactive run | `--print` | `exec` | `--print` |
| Structured result | `--json-schema` -> `structured_output` (**verified live**) | `--output-schema` + `--output-last-message` (documented; live run blocked by the account's usage limit tonight) | `--json-schema` -> `structured_output` (**verified live**) |
| Session id | `--session-id` (Hipson sets uuid5(task/assignment)); `--resume` (**verified live**) | `thread.started` event; `exec resume` (documented) | `conversation_id`; `--conversation` (documented) |
| Usage telemetry | `usage`, `modelUsage`, `total_cost_usd` (a list-price estimate, not a subscription charge) | `turn.completed.usage` with `--json` | `usage` |
| Read-only / write | `--permission-mode plan` / `acceptEdits` | `--sandbox read-only` / `workspace-write` | `--mode plan` / `accept-edits` |
| Project instructions | `CLAUDE.md` honoured (**verified live**) | `AGENTS.md` (documented) | `AGENTS.md`/`GEMINI.md` honoured (**verified live**; which file is not isolated) |
| Controlled isolation | `--setting-sources project --strict-mcp-config --no-session-persistence`; `--bare` needs `ANTHROPIC_API_KEY` | `--ephemeral --ignore-user-config --ignore-rules` | **unsupported**: no documented configuration-free run |
| Auth status | `claude auth status` (JSON) | `codex login status` | none documented |

## Profiles

- **native** (default): the runtime runs with the user's own configuration, plugins, skills, MCP servers,
  instructions and model defaults. Hipson adds no clean-room flag and never a permission bypass
  (`--dangerously-*`). It adds two things: the worker rules and result contract in the prompt, and for Claude
  a task-scoped permission for exactly the lead's declared checks (`--allowedTools "Bash(<check>)"`, from
  `HIPSON_FLOW_CHECKS`), so a worker can run the acceptance command without a wider grant.
- **controlled**: for benchmarks and isolated tests; uses each runtime's documented isolation flags. Antigravity
  refuses it (exit 64, `unsupported`) rather than pretending. The e2e harness keeps its own stronger isolation
  (temporary `HOME`, `CODEX_HOME`, PATH proxy).

## What `hipson agent exec` guarantees

- Runs only inside a recorded assignment (`HIPSON_ROLE=worker`, `HIPSON_TASK`, `HIPSON_ASSIGNMENT`); otherwise
  exit 64.
- The structured answer must validate against `schemas/task-result.schema.json`; Hipson writes it to
  `HIPSON_FLOW_RESULT`. Anything else is a failure, never a success.
- Failure classes from the runtime's own words and exit codes: `rate_limited` (5), `auth_required` (4),
  `permission_denied` (6), `malformed_output` (2), `timeout` (124), `provider_error` (3), `unsupported` (64). `task
  run` records the exit code; the flow stops with `worker_failed` / `reviewer_failed`.
- Evidence beside the result (`<result>.meta.json`): runtime, profile, role, session, models, usage, permission
  denials, duration, brief size and a bytes/4 token estimate.
- Sessions: a new run gets a session id bound to its task and assignment; `--continue-session` resumes only this
  flow's latest successful session of the same runtime, task and role. Another task's context is never reused.

## Limits

- Coordination, not security: actor labels are not authenticated; the worker rules in the prompt are a
  convention a model may ignore. The enforced boundaries are the runtimes' own sandboxes/permission modes and
  `task run` (containment, observation, `--env minimal`).
- Native mode inherits the user's permission rules: a worker may be unable to run commands it was not granted.
  It is told to report that under `risks`; the lead's verification is what accepts the work.
- Native subagents and plugins of a worker run are the runtime's business; Hipson bounds the assignment, not
  what the runtime does inside it.
