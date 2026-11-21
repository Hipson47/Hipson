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

## Capability matrix (live evidence, 2026-11-19, WSL2)

Installed: Claude Code 2.1.319, Codex CLI 0.169.0, Antigravity 1.5.12. "Verified" means a run exercised it and
the probe observed the effect itself (a file, a hook log line, a re-run check, a recalled code word), not the
model's report. Evidence: `scripts/native_probe.py` reports in [evals/](evals/) (`native-probe-2026-11-19-*.json`),
one sample per cell; "Hipson = manual" means the run through `hipson task run ... -- hipson agent exec` and the
runtime's own non-interactive command on the same fixture showed the same capability.

| | Claude Code | Codex | Antigravity |
| --- | --- | --- | --- |
| Structured result | verified (`--json-schema`), Hipson = manual | verified (`--output-schema`); first live Codex runs through Hipson | verified (`--json-schema`) |
| Project instructions | verified: `CLAUDE.md`, Hipson = manual | verified: `AGENTS.md`, Hipson = manual | verified: `GEMINI.md` (reviewer, sessions, manual builder) |
| Project skills | verified: `.claude/skills`, Hipson = manual | verified: `.agents/skills`, Hipson = manual | unverified: no documented project skill directory |
| Hooks | verified: project `PostToolUse` hook ran (8 events), Hipson = manual | not probed | not probed |
| Native subagents | verified: the worker delegated to `Explore` (hook saw the Agent tool), Hipson = manual | not observed (none requested by the runtime) | not probed |
| Write mode (builder) | verified: edit + task-scoped grant for the declared check | verified: `workspace-write` | verified: `accept-edits` + session-only `command(<check>)` grants for the declared checks. Hipson merges these grants with the user's allow rules. |
| Read-only mode (reviewer) | verified: plan mode, the write did not happen | verified: read-only sandbox refused the write ("Read-only file system") | **not enforced**: in `--mode plan` the reviewer created the file in 2 of 2 probe runs |
| Session continuation | verified: `--resume`, code word recalled; also from another working directory (review snapshots) | verified after a fix: `exec resume` refuses `--sandbox`, so Hipson now passes `-c sandbox_mode=...` (same mode) | verified: `--conversation`, code word recalled |
| A missing session | `No conversation found with session ID` (exit 1): Hipson reports it and the next `--continue-session` starts a new session | refused outside a trusted or Git directory; snapshots are Git checkouts | not probed |
| Usage telemetry | `usage`, `modelUsage` (models used), turns | `turn.completed.usage` (input includes cached input); no model or turn count in `exec --json` | `usage`, turns; no model name |
| MCP tools, plugins | configured and listed (`hipson agent runtimes`); not execution-verified | configured and listed; not execution-verified | configured and listed; not execution-verified |
| Code intelligence, browser, computer use | not exercised by headless workers; unverified | unverified | unverified |
| Controlled isolation | `--setting-sources project --strict-mcp-config --no-session-persistence`, refused when the project has `.claude/settings*.json` (they would load); `--bare` needs an API key | `--ephemeral --ignore-user-config --ignore-rules` | **unsupported**: no documented configuration-free run |
| Auth status | `claude auth status` (JSON); logged-out runs are refused before launch | `codex login status`; same preflight | none documented; the first run is the evidence |

Discrepancies found and how they were classified:

- Codex `exec resume` with `--sandbox` failed immediately (implementation defect in Hipson; fixed).
- Antigravity plan mode does not prevent writes (provider limitation). Hipson's review integrity does not rest on
  it: a reviewer's write lands in its snapshot, which then fails verification (`snapshot gained ... file(s)` or
  `changed during the review`), and a write to the project is an observed change by a read-only assignment.
  Prefer Claude or Codex as reviewers where a write must be prevented rather than detected.
- Antigravity aborts its whole headless response when any command it tries is not granted (provider
  limitation): a builder that also runs, say, `git diff` ends `permission_denied` even after fixing the code (2 of
  2 Hipson probe runs, 1 of 2 manual). The check grant removes the most common cause; `--retry` re-runs it.
- Codex reports no model or turn count in `exec --json` (provider limitation): those fields are `null`.
- Claude per-project entries keyed by path (`~/.claude.json` projects) and Codex trusted-project settings do not
  apply to a review snapshot directory (configuration issue by design of path-keyed trust); files in the
  project (`CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, skills) do, since the snapshot contains them.

## First workflow

1. `hipson agent runtimes`: which CLIs are installed, whether their own status command says you are logged in
   (Antigravity has none: its first run is the evidence), and which capabilities are documented or verified.
   Log in with the runtime itself; Hipson never handles credentials.
2. Commit the project's current state (verification binds to a commit), then write `flow.json`:

```json
{
  "version": 1,
  "title": "Fix the pair parser",
  "objective": "parse_pair splits on ':' and trims whitespace around both parts",
  "checks": [["python3", "tests/acceptance.py"]],
  "builder": {"actor": "claude", "scope": ["src/parser"],
              "command": ["hipson", "agent", "exec", "--runtime", "claude", "--role", "builder"]},
  "reviewer": {"actor": "codex",
               "command": ["hipson", "agent", "exec", "--runtime", "codex", "--role", "reviewer"]},
  "max_repairs": 1
}
```

   On Windows write the check as `["python", "tests/acceptance.py"]`: `python3` there is usually the Microsoft
   Store alias, which runs no Python.

   With only one coding agent, use it for both roles under two labels (replace the `builder` and `reviewer`
   entries above); the reviewer still runs read-only on a
   snapshot and must differ from the builder's label (verified live with Claude Code on 2026-11-19: accepted in
   24 s, review recorded `source=snapshot`):

```json
  "builder": {"actor": "claude", "scope": ["src/parser"],
              "command": ["hipson", "agent", "exec", "--runtime", "claude", "--role", "builder"]},
  "reviewer": {"actor": "claude-reviewer",
               "command": ["hipson", "agent", "exec", "--runtime", "claude", "--role", "reviewer"]}
```

3. `hipson task flow --spec flow.json --check` validates it without writing anything; `hipson task flow --spec
   flow.json` runs it: build, lead verification of the checks, an independent review of an immutable snapshot
   of the verified tree, at most `max_repairs` repairs, and a stop in `review` for your final `hipson task close`.
4. Interrupted or stopped: `hipson task show --task T` says why; `hipson task flow --spec flow.json --resume T`
   continues from the ledger without repeating finished steps (`--retry` re-runs a failed worker once).

Permissions: a builder runs with the runtime's write mode inside its declared scope (Claude `acceptEdits` plus a
grant for exactly the declared checks; Codex `workspace-write`; Antigravity `accept-edits`), a reviewer with its
read-only mode, whatever the command line says. Your own permission rules, plugins, skills, MCP servers and
model defaults apply (profile `native`). The reviewer starts in its snapshot directory, so project settings that
a runtime keys by path (Claude's per-project entries in `~/.claude.json`, Codex trusted projects) do not apply
to the review; files in the project (`CLAUDE.md`, `AGENTS.md`, `.claude/settings.json`, skills) do.

Common failures: `auth_required` (log in with the runtime), `rate_limited` (wait, or use another runtime for that
role), `permission_denied` (the runtime refused a tool: Antigravity's headless plan mode cannot run commands, so
reviewers get the diff in their brief), `model_unavailable` (choose a model from Antigravity's diagnostic),
`unsupported` (a combination the runtime does not document, e.g.
controlled Antigravity), `needs_lead` with "changed protected paths" (the builder edited a check's test or
runtime configuration: restore it with `git checkout -- PATH`, then resume).

## Profiles

- **native** (default): the runtime runs with the user's own configuration, plugins, skills, MCP servers,
  instructions and model defaults. Hipson adds no clean-room flag and never a permission bypass
  (`--dangerously-*`). It adds two things: the worker rules and result contract in the prompt, and for a
  Claude or Antigravity writer a task-scoped permission for exactly the task's declared checks, read from the
  ledger. Claude uses `--allowedTools "Bash(<check>)"`. Antigravity adds `command(<check>)` to the
  `permissions.allow` from `~/.gemini/antigravity-cli/settings.json` in a temporary `--settings` file with
  mode `0600` on POSIX. Hipson passes the file path, removes the file after the run, and leaves the user's
  settings untouched. If Hipson cannot parse an existing allow list, it omits the layer. The layer contains
  only `permissions.allow`. Deny inheritance depends on Antigravity's layer merge and is a live check for the
  lead. A check with a wildcard, bracket or leading `NAME=value` word gets no automatic grant.
  The permission mode follows the recorded assignment (`ro` -> plan / read-only / plan mode), whatever flags say.
- **controlled**: for benchmarks and isolated tests; uses each runtime's documented isolation flags. Antigravity
  refuses it (exit 64, `unsupported`) rather than pretending. The e2e harness keeps its own stronger isolation
  (temporary `HOME`, `CODEX_HOME`, PATH proxy).

## What `hipson agent exec` guarantees

- Runs only inside a recorded assignment (`HIPSON_ROLE=worker`, `HIPSON_TASK`, `HIPSON_ASSIGNMENT`); otherwise
  exit 64.
- The structured answer must validate against `schemas/task-result.schema.json`; Hipson writes it to
  `HIPSON_FLOW_RESULT`. Anything else is a failure, never a success.
- Failure classes from the runtime's own words and exit codes: `rate_limited` (5), `auth_required` (4),
  `permission_denied` (6), `malformed_output` (2), `timeout` (124), `provider_error` (3), `unsupported` (64),
  and `model_unavailable` (64) for Antigravity model selection failures. The redacted CLI diagnostic is printed
  and saved in the meta file's `detail`. `task run` records the exit code and the flow stops with
  `worker_failed` / `reviewer_failed`.
- Evidence beside the result (`<result>.meta.json`): runtime, profile, role, mode, ledger, project, session,
  models, usage, the names of denied tools (never their inputs), duration, brief size and a bytes/4 token
  estimate. A nonzero exit is never a success, even after a valid-looking result; Codex's last-message file is
  removed before and after each run so a stale one cannot be ingested. `verified` evidence records the CLI
  version and counts only while that version is installed.
- Sessions: a new run gets a fresh session id, recorded with its task and assignment in the meta file;
  `--continue-session` resumes only the latest successful session of the same ledger, project (`HIPSON_PROJECT`,
  so a reviewer in a snapshot keeps its project identity), runtime, task and role, read from regular meta files
  beside the result (never through a link) whose assignment passed in the ledger, and only a well-formed id.
  A session whose later resume failed (gone at the provider) is skipped, so the next run starts a new one.
  Resuming never widens permissions: the mode comes from the recorded assignment on every run. Limit: the
  session id itself is not in the ledger, so a process that can write the flow's private directory could
  substitute another id of the same runtime (the assignment check still binds task and role).
- State files (schema, result, meta) are never written through a symlink or to a non-regular file; a launch
  that fails (`OSError`) is a `provider_error` with meta; an Antigravity prompt over 100,000 bytes (it is an argv
  element) is refused before launch, and so is an agy that resolves to a `.cmd`/`.bat` script on Windows
  (cmd.exe would cut the multi-line prompt at its first line break).

## Live runs through Hipson (2026-11-19, one sample each; not a benchmark)

- Native probe: see the matrix above and `docs/evals/native-probe-2026-11-19-*.json`. Token use of the same
  builder brief, Hipson vs manual (one sample each, cache state differs between runs): Claude 41.0k cache-write +
  198.3k cache-read input vs 26.5k + 170.7k; Codex 57.7k input (38.0k cached) vs 116.8k (96.6k cached).
- Interrupted flow, then resumed (`task flow`, Claude builder with `--continue-session`, Codex reviewer): SIGTERM
  5 s into the build stopped the flow with `interrupted` (exit 130) and the builder `cancelled`; `--resume`
  acknowledged it, re-ran the builder once (not counted as a repair), verified, reviewed the snapshot and stopped
  in `review` (exit 0); the hidden acceptance check passed. The interrupted session had no successful result, so
  the re-run started a new session instead of resuming a broken one.
- Live A/B/C comparison: [FLOW_EVAL.md](FLOW_EVAL.md#live-comparison).

## Limits

- Coordination, not security: actor labels are not authenticated; the worker rules in the prompt are a
  convention a model may ignore. The enforced boundaries are the runtimes' own sandboxes/permission modes and
  `task run` (containment, observation, `--env minimal`).
- Native mode inherits the user's permission rules: a worker may be unable to run commands it was not granted.
  It is told to report that under `risks`; the lead's verification is what accepts the work.
- Native subagents and plugins of a worker run are the runtime's business; Hipson bounds the assignment, not
  what the runtime does inside it.
