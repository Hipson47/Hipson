# Hipson 1.3.0

Hipson 1.3.0 removes the two steps that made a first setup and every worker launch depend on
shell knowledge: one command sets a project up, one command runs and records a worker, on Linux,
macOS and native Windows.

Status: **beta**. Hipson coordinates cooperating agents; it is not a security boundary or a sandbox.

## Highlights

- **`hipson init`**: checks prerequisites, creates or migrates the task ledger, registers the Hipson
  MCP server with Claude Code and/or Codex, verifies it with an `initialize` + `tools/list`
  handshake and prints the first-task commands.
  - Planning and `--dry-run` only read files: no client CLI runs and no MCP server starts before
    you agree. Nothing changes without an interactive "y" or `--yes`.
  - Existing registrations are compared by scope, command, arguments, working directory,
    environment (the ledger path) and tool filters; a registration that serves another project is
    reported as a conflict, never as "already configured".
  - Config edits are locked across processes, checked against the planned snapshot, written
    atomically, validated, and rolled back only when the file is still what Hipson wrote. Backups
    are private and unique.
- **`hipson task run`**: a cross-platform worker runner with the guarantees of
  `examples/agent-run.sh`: project binding (exit 76), live claim refusal (exit 75), owner identity,
  signal forwarding, structured-result ingestion with a safe fallback, Codex rollout facts.
  - Windows: the worker is created inside a Job Object, so hard-killing the runner kills the whole
    worker tree (verified with 20 kills during startup: no orphans).
  - POSIX: a private session/group even when the runner already leads a group, and Linux
    subreaping keeps claims while a detached descendant is still alive.

## Reviews

Both commands went through independent read-only reviews before release; every finding has a
regression test that fails on the code before its fix.

| Area | Review findings fixed |
|---|---|
| `hipson task run` | Windows Job membership not atomic at creation; runner could signal unrelated processes in the caller's group; detached descendants escaped; result-file parser limits aborted the finish |
| `hipson init` | dry-run started MCP servers through `claude mcp get`; current Claude CLI wording broke fresh setup; concurrent or partial config writes; backup permissions; Codex project layers, environment, cwd and tool filters ignored; blind rollback; per-project disablement ignored |

## Install

```bash
uv tool install hipson
hipson init
```

Per-OS details: [docs/INSTALL.md](INSTALL.md).

## Limits

- Detached POSIX descendants of client subprocesses started by `hipson init` are cleaned up only
  within their process group; `hipson task run` covers detached descendants on Linux, best effort
  on macOS.
- Native Windows has no boot identity: after a reboot a reused PID can hold a claim until its lease
  expires or the lead finishes it.
