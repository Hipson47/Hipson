# Agent Autopilot Layer

Hipson Agent Autopilot Layer v0 turns the CLI control plane into an
agent-discoverable workflow layer. The coding agent remains Codex, Cursor,
Claude Code, or an MCP client. Hipson supplies the local contract, packet
boundary, verification evidence, policy, and handoff artifacts.

## Default Loop

```text
agent bootstrap -> contract -> work plan -> packet -> preflight -> verify -> quality report -> evidence -> audit -> summary
```

The local-first command is:

```bash
hipson autopilot review --task "review current diff" --json
```

For bounded implementation work, use an executor packet with explicit edit
scope:

```bash
hipson autopilot implement --task "implement bounded parser fix" --allowed-edit src/hipson/parser.py,tests --verification "git diff --check" --json
```

It writes:

```text
runs/<work_id>/
  contract.json
  work.json
  review-packet.md
  preflight.json
  verify.json
  quality.json
  evidence.jsonl
  audit.json
  summary.md
```

Default execution is provider-free. `--ai-profile <name>` prepares the advisory
sidecar path. A real provider-backed sidecar requires explicit `--run-sidecar`.

Runs can be resumed without rebuilding the packet or work plan:

```bash
hipson autopilot resume --run runs/<work_id> --rerun-step verify --json
```

`--rerun-step` accepts `contract`, `preflight`, `verify`, `quality`,
`quality_eval`, `evidence`, `audit`, or `summary`, and can be repeated. This is
for repairing or refreshing missing/stale artifacts without repeating the full
workflow.

## Agent Bootstrap

Agents can discover Hipson through:

```bash
hipson agent bootstrap --target codex --json
hipson agent bootstrap --target cursor --json
hipson agent bootstrap --target claude --json
```

The bootstrap artifact is `hipson.agent_bootstrap`. It reports the detected
project, contract availability, recommended first command, installed surfaces,
warnings, policy summary, and fallback commands.

## MCP Stdio

`hipson mcp serve --catalog` exposes a read-first catalog for MCP clients, and
`hipson mcp serve --stdio` starts a minimal line-delimited JSON-RPC stdio
server. The current tool set is:

- `contract.show`;
- `policy.show`;
- `work.create`;
- `packet.preflight`;
- `verify.run`;
- `quality.report`;
- `evidence.append`;
- `audit.show`.

Read-first tools are provider-free. `verify.run` and `evidence.append` are gated
and require `approved=true` in the tool arguments before they execute local
commands or write evidence. Provider-backed review remains an explicit sidecar
action; MCP never hides provider calls.

## Project Policy Enforcement

Autopilot checks `.hipson/policy.json` or `.hipson/policy.yaml` before running.
Invalid policy blocks the run. `denied_paths` block autopilot when the current
git diff touches protected files. `local_only: true` blocks provider-backed
sidecars even if `--run-sidecar` is passed. Prompt-required operations must be
explicitly approved through the CLI.

## Agent Surfaces Doctor

Use:

```bash
hipson doctor --agent-surfaces --json
```

It reports whether Codex, Cursor, Claude, and MCP notes are installed, whether
the current project policy is valid, whether the agent contract is available,
and the recommended next command.

## Human Gate

Hipson can automate local evidence collection. It cannot approve release,
security, destructive, credential, or irreversible decisions. Those remain human
gates even when local verification passes.
