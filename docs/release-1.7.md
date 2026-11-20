# Hipson 1.7.0

Hipson 1.7.0 lets Hipson drive the coding agents you already use, Claude Code, Codex or Antigravity, through a
complete, resumable loop: build, run your acceptance checks, an independent review of exactly the tree that
passed them, and bounded repairs. Nothing closes on a worker's word, and nothing in the core needs WSL, Bash or a
paid API.

Status: **beta**.

## Highlights

- **`hipson task flow --spec flow.json`**: one deterministic workflow per task: builder, lead verification of the
  declared checks, an independent read-only reviewer, at most `max_repairs` evidence-driven repairs, and a stop
  in `review` for your final `hipson task close`. Every decision is read from the ledger, so `--resume` continues
  an interrupted flow without repeating finished steps. Works with a single agent (same runtime, two labels).
- **Required review on an immutable snapshot**: `task open --require-review` (every flow task) makes `done` need
  a review of a private, read-only copy of the exact candidate the checks accepted. A reviewer can no longer be
  shown one tree and close another.
- **Protected paths**: a builder that edits the tests your checks run, or the agents' project configuration
  (`CLAUDE.md`, `AGENTS.md`, `.claude`, `.codex`, `.mcp.json`, ...), stops the flow for you to decide.
- **Native agent runtimes**: `hipson agent runtimes` shows what is installed, logged in and verified for Claude
  Code, Codex and Antigravity, and how each would run on this machine (native, a Windows launcher, or a launcher
  that forwards to WSL). `hipson agent exec` runs them as workers with your own configuration, skills, plugins
  and models, and validates their structured results.
- **Native Windows, macOS and Linux**: CI runs both test runners and a clean-wheel smoke of the whole lifecycle
  on all three. WSL2 is optional and kept separate from Windows.
- Also new since 1.6: opt-in change observation (`--observe`), worker environment allowlist (`--env minimal`),
  pre-run checkpoints and restore (`--checkpoint`), actor routing (`hipson route actor`), case-insensitive claims
  on Windows and macOS. Full list: [CHANGELOG.md](../CHANGELOG.md).

## Upgrade from 1.6

```bash
uv tool upgrade hipson        # or: pip install --upgrade hipson
hipson init --client none     # migrates the ledger to schema 11 after writing a backup next to it
```

1.7 reads a 1.6 ledger before migration. After `hipson init` migrates it, 1.6 can no longer open it: upgrade every
Hipson that shares the ledger (including the one your MCP client launches) and restart the lead client. Verified
on a disposable 1.6.0 ledger from PyPI: closed and open tasks, assignments and events carried over, and an open
task was completed after the upgrade.

Event types `flow`, `review_policy` and `reviewed` are now reserved: custom `hipson task event --type` calls using
them are refused.

## Platform support

| | Linux | macOS | Windows native |
| --- | --- | --- | --- |
| Install, init, MCP, tasks, verification, recovery, flow | CI | CI | CI |
| Live agent runs | Claude Code, Codex, Antigravity | not run live | Codex |

Details, evidence and limits: [PLATFORMS.md](PLATFORMS.md). Antigravity's plan mode is not a read-only sandbox:
Hipson detects a reviewer's write (snapshot check, observation) but cannot prevent it; prefer Claude Code or Codex
as reviewers where that matters.

## Known limitations

- Coordination, not security: actor labels are not authenticated and the worker rules are instructions.
- The review guarantee covers what Hipson gives the reviewer; a reviewer that reads the live project by absolute
  path is outside it.
- On small, well-specified tasks with a capable model, a flow costs about 2 to 4 times a plain agent run (mostly
  the mandatory review) with no measurable quality gain in our live samples; its gains show when work goes
  wrong (see [FLOW_EVAL.md](FLOW_EVAL.md)).
- Windows has no boot identity: after a reboot a reused PID can keep a claim until its lease ends.
